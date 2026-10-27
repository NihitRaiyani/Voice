"""Privacy-preserving analytics over bounded durable SQL evidence."""

from roma.repositories.postgres.analytics import AnalyticsPostgresRepository
from roma.schemas.analytics import AnalyticsQuery


class AnalyticsService:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    async def _report(self, name, start_date, end_date):
        query = AnalyticsQuery(start_date=start_date, end_date=end_date)
        start, end = query.bounds()
        async with self.session_factory() as session, session.begin():
            data = await getattr(AnalyticsPostgresRepository(session), name)(start, end)
        return {
            "window": {"start": start.isoformat(), "end": end.isoformat(), "timezone": "UTC"},
            **data,
        }

    async def overview(self, *, start_date=None, end_date=None):
        return await self._report("overview", start_date, end_date)

    async def funnel(self, *, start_date=None, end_date=None):
        return await self._report("funnel", start_date, end_date)

    async def branches(self, *, start_date=None, end_date=None):
        return await self._report("branches", start_date, end_date)
