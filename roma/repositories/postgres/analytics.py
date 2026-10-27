"""SQL aggregates only: call cohorts use started_at in a half-open UTC window."""

from sqlalchemy import String, case, cast, func, select

from roma.repositories.postgres.costs import CostPostgresRepository
from roma.repositories.postgres.models import (
    Appointment,
    Branch,
    Call,
    CallEvent,
    CallTurn,
    SafetyEvent,
)

TERMINAL_UNSUCCESSFUL = ("failed", "cancelled", "no_answer", "busy")
LANGUAGES = ("gu", "hi", "en", "code_mix", "unknown")
COMMITTED = ("booked", "confirmed", "completed")


class AnalyticsPostgresRepository:
    def __init__(self, session):
        self.session = session

    def _cohort(self, start, end):
        return (
            select(Call.id).where(Call.started_at >= start, Call.started_at < end).cte("cohort")
        )

    async def overview(self, start, end):
        cohort = self._cohort(start, end)
        turns = (
            select(CallTurn.call_id, func.count().label("turns"))
            .where(CallTurn.call_id.in_(select(cohort.c.id)))
            .group_by(CallTurn.call_id)
            .subquery()
        )
        valid_duration = case(
            (
                Call.ended_at >= Call.started_at,
                func.extract("epoch", Call.ended_at - Call.started_at),
            )
        )
        total, completed, abandoned, duration, average_turns = (
            await self.session.execute(
                select(
                    func.count(),
                    func.count().filter(Call.status == "completed"),
                    func.count().filter(Call.status.in_(TERMINAL_UNSUCCESSFUL)),
                    func.avg(valid_duration),
                    func.avg(func.coalesce(turns.c.turns, 0)),
                )
                .select_from(Call)
                .outerjoin(turns, turns.c.call_id == Call.id)
                .where(Call.id.in_(select(cohort.c.id)))
            )
        ).one()
        conversion = await self.session.scalar(
            select(func.count(func.distinct(Appointment.call_id))).where(
                Appointment.call_id.in_(select(cohort.c.id)), Appointment.status.in_(COMMITTED)
            )
        )
        latency = (
            await self.session.execute(
                select(
                    func.count(CallTurn.latency_ms),
                    func.count().filter(CallTurn.latency_ms.is_(None)),
                    func.avg(CallTurn.latency_ms),
                    *(
                        func.percentile_cont(q).within_group(CallTurn.latency_ms)
                        for q in (0.5, 0.95, 0.99)
                    ),
                ).where(CallTurn.call_id.in_(select(cohort.c.id)))
            )
        ).one()
        languages = dict.fromkeys(LANGUAGES, 0)
        language = case((Call.language.in_(LANGUAGES[:-1]), Call.language), else_="unknown")
        for name, count in (
            await self.session.execute(
                select(language, func.count())
                .where(Call.id.in_(select(cohort.c.id)))
                .group_by(language)
            )
        ).all():
            languages[name] = count
        rules = (
            await self.session.execute(
                select(func.coalesce(SafetyEvent.rule, "unknown"), func.count())
                .where(SafetyEvent.call_id.in_(select(cohort.c.id)))
                .group_by(SafetyEvent.rule)
                .order_by(SafetyEvent.rule)
            )
        ).all()
        return {
            "calls": {
                "total": total,
                "completed": completed,
                "abandoned": abandoned,
                "active": total - completed - abandoned,
            },
            "converted_calls": conversion,
            "appointment_conversion_rate": conversion / total if total else None,
            "average_duration_seconds": float(duration) if duration is not None else None,
            "average_turns": float(average_turns) if average_turns is not None else None,
            "latency_ms": dict(
                zip(
                    ("samples", "missing", "average", "p50", "p95", "p99"),
                    (float(x) if x is not None else None for x in latency),
                    strict=True,
                )
            ),
            "languages": languages,
            "safety_rules": [{"rule": rule, "count": count} for rule, count in rules],
            "costs": await CostPostgresRepository(self.session).summary(start=start, end=end),
            "definitions": {
                "cohort": "calls started within the UTC window; all directions",
                "completed": "status completed",
                "abandoned": "terminal status failed, cancelled, no_answer or busy",
                "conversion": "distinct cohort calls with SQL appointment status booked, confirmed or completed / total calls",
                "duration": "ended_at minus started_at for nonnegative known durations",
                "turns": "persisted CallTurn rows per cohort call, including zero",
                "latency": "known CallTurn.latency_ms; missing samples excluded from numeric aggregates",
                "costs": "normalized cost-engine usage occurring within window, independent of call start; currencies never combined; average denominator is calls with priced usage",
            },
        }

    async def funnel(self, start, end):
        # A later stage alone cannot prove any previous milestone. Each next
        # timestamp must have an actual event at/after its observed predecessor.
        current = (
            select(Call.id.label("call_id"), Call.started_at.label("observed_at"))
            .where(Call.started_at >= start, Call.started_at < end)
            .cte("incoming")
        )
        stages = [("incoming", current)]
        for stage in (
            "greeting_completed",
            "discovery_completed",
            "value_presented",
            "booking_offered",
            "slot_accepted",
        ):
            observed = (
                select(func.min(CallEvent.occurred_at))
                .where(
                    CallEvent.call_id == current.c.call_id,
                    CallEvent.event_type == f"analytics.{stage}",
                    CallEvent.occurred_at >= current.c.observed_at,
                    CallEvent.occurred_at < end,
                )
                .correlate(current)
                .scalar_subquery()
            )
            current = select(current.c.call_id, observed.label("observed_at")).cte(stage)
            stages.append((stage, current))
        # A fake event, checkpoint appointment_id, or locked_slot never confirms.
        committed = (
            select(func.min(Appointment.created_at))
            .where(
                Appointment.call_id == current.c.call_id,
                Appointment.status.in_(COMMITTED),
                Appointment.created_at >= current.c.observed_at,
                Appointment.created_at < end,
            )
            .correlate(current)
            .scalar_subquery()
        )
        confirmed = select(current.c.call_id, committed.label("observed_at")).cte(
            "appointment_confirmed"
        )
        stages.append(("appointment_confirmed", confirmed))
        counts = (
            await self.session.execute(
                select(
                    *(
                        select(func.count(cte.c.observed_at)).scalar_subquery()
                        for _, cte in stages
                    )
                )
            )
        ).one()
        return {
            "stages": [
                {"stage": name, "calls": count}
                for (name, _), count in zip(stages, counts, strict=True)
            ],
            "evidence": "ordered persisted milestones; SQL appointment required for confirmation",
            "definitions": {
                "incoming": "all cohort calls, including outbound; incoming means entered the funnel",
                "milestones": "analytics.<stage> CallEvents observed in order before window end; missing history is not inferred",
                "appointment_confirmed": "SQL appointment status booked, confirmed or completed created after observed slot acceptance and before window end",
            },
        }

    async def branches(self, start, end):
        cohort = self._cohort(start, end)
        # A call is attributable only when its SQL appointments name one unique
        # branch. Neither caller city nor free-text checkpoint fields are read.
        attribution = (
            select(
                Appointment.call_id,
                case(
                    (
                        func.count(func.distinct(Appointment.branch_id)) == 1,
                        func.min(cast(Appointment.branch_id, String)),
                    ),
                    else_=None,
                ).label("branch_id"),
            )
            .where(Appointment.call_id.in_(select(cohort.c.id)))
            .group_by(Appointment.call_id)
            .subquery()
        )
        calls = (
            await self.session.execute(
                select(Branch.id, Branch.name, func.count())
                .select_from(Call)
                .outerjoin(attribution, attribution.c.call_id == Call.id)
                .outerjoin(Branch, cast(Branch.id, String) == attribution.c.branch_id)
                .where(Call.id.in_(select(cohort.c.id)))
                .group_by(Branch.id, Branch.name)
            )
        ).all()
        appointments = (
            await self.session.execute(
                select(Branch.id, Branch.name, func.count())
                .select_from(Appointment)
                .join(Branch, Branch.id == Appointment.branch_id)
                .where(
                    Appointment.created_at >= start,
                    Appointment.created_at < end,
                    Appointment.status.in_(COMMITTED),
                )
                .group_by(Branch.id, Branch.name)
            )
        ).all()
        grouped = {
            None: {"branch_id": None, "branch_name": "unknown", "calls": 0, "appointments": 0}
        }
        for metric, rows in (("calls", calls), ("appointments", appointments)):
            for identifier, name, count in rows:
                key = str(identifier) if identifier is not None else None
                grouped.setdefault(
                    key, {"branch_id": key, "branch_name": name, "calls": 0, "appointments": 0}
                )[metric] = count
        return {
            "branches": [
                grouped[key]
                for key in sorted(grouped, key=lambda key: (key is None, key or ""))
            ],
            "definitions": {
                "calls": "call-start cohort attributed via one unique SQL appointment branch; absent or ambiguous links are unknown",
                "appointments": "SQL appointments created within the window with status booked, confirmed or completed, including appointments without a linked call",
            },
        }
