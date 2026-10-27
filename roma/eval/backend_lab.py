"""Offline full-text-call FastAPI workload using production controller and safety seams.

Never mounted by production. SQL and Redis are optional explicitly owned disposable
service probes, not evidence that every domain write was exercised under load.
"""
from __future__ import annotations

import json
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI

from roma.domain.appointments.slots import TimeSlot
from roma.domain.conversation.state import CallState
from roma.domain.safety.filter import screen
from roma.eval.runner import FROZEN_NOW
from roma.eval.script import Script
from roma.providers.ai.mocks import MockLLMProvider
from roma.services.text_conversation_service import TextConversationService


class ScriptedLLM:
    """Mock model output is fixture-driven; no semantic/model-quality claim."""
    def __init__(self):
        self.turn=None
        self.ttft_ms=[]
        self.json_valid=0
        self.json_total=0
        self.requests=0

    async def generate(self,request):
        self.requests+=1
        turn=self.turn
        if request.response_schema:
            if 'value' in request.response_schema.get('properties',{}):
                value={'value':turn.slot,'confidence':.95 if turn.slot else 0.,'raw':''}
            else:
                value=TimeSlot(accepted=turn.accept or turn.chose_offer is not None,
                    chose_offer=turn.chose_offer,readback_confirmed=turn.confirm,
                    day_offset=1 if turn.accept else None,
                    hour=(turn.accept_hour or 5) if turn.accept else None,
                    period=(turn.accept_period or 'evening') if turn.accept else None,
                    confidence=.95).model_dump()
            text=json.dumps(value)
            self.json_total+=1
            json.loads(text)
            self.json_valid+=1
        else:
            text=turn.roma or 'Ji, main aapki baat samajh rahi hoon.'
        started=perf_counter()
        first=True
        async for chunk in MockLLMProvider(text,chunk_size=24).generate(request):
            if first and chunk.text:
                self.ttft_ms.append((perf_counter()-started)*1000)
                first=False
            yield chunk

    async def aclose(self):
        return None


async def run_text_call(script: Script):
    provider=ScriptedLLM()
    state=CallState(call_sid=f'eval-{uuid4()}',lead_name=script.lead_name,branch=script.branch)
    service=TextConversationService(provider)
    stage_hits=0
    stage_total=0
    provider_errors=0
    unsafe=0
    for turn in script.turns:
        provider.turn=turn
        trace=await service.turn(state,turn.lead,now=FROZEN_NOW,elapsed_secs=turn.elapsed)
        provider_errors+=int(trace.provider_error is not None)
        unsafe+=int(not screen(trace.final_response).allowed)
        if turn.expect_stage:
            stage_total+=1
            stage_hits+=int(state.stage==turn.expect_stage)
    return {'turns':len(script.turns),'stage_hits':stage_hits,'stage_total':stage_total,
            'provider_errors':provider_errors,'unsafe_final_sentences':unsafe,
            'mock_ttft_ms':provider.ttft_ms,'json_valid':provider.json_valid,
            'json_total':provider.json_total,'model_requests':provider.requests,
            'input_tokens':None,'output_tokens':None,'sql_booking_committed':False}


def create_lab_app(script: Script, *, engine=None, redis=None):
    app=FastAPI(title='Roma isolated mock call load lab')

    @app.post('/lab/call')
    async def call():
        db_connections=None
        if engine is not None:
            from sqlalchemy import text
            async with engine.connect() as connection:
                await connection.execute(text('SELECT 1'))
                db_connections=await connection.scalar(text(
                    'SELECT count(*) FROM pg_stat_activity WHERE datname=current_database()'))
        redis_ms=None
        if redis is not None:
            started=perf_counter()
            await redis.ping()
            redis_ms=(perf_counter()-started)*1000
        summary=await run_text_call(script)
        return {'fallback':bool(summary['provider_errors']),'quality_ok':not summary['unsafe_final_sentences'],
                'redis_ms':redis_ms,'rag_ms':None,'db_connections':db_connections,**summary}

    return app
