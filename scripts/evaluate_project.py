"""Build a fail-closed readiness report and optional public/mock-only experiments."""
import argparse
import asyncio
import hashlib
import json
import platform
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from roma.eval.backend_lab import run_text_call
from roma.eval.benchmark_evidence import read_report
from roma.eval.checks import CANARIES, check_canaries
from roma.eval.load import percentiles
from roma.eval.project_readiness import build_readiness, fingerprint, load_matrix
from roma.eval.regressions import regression_receipts, run_regressions
from roma.eval.runner import run_script
from roma.eval.script import load_dir

ROOT=Path(__file__).resolve().parents[1]


async def offline_experiment():
    scripts=load_dir(ROOT/'evals/scripts')
    results=[await run_script(script) for script in scripts]
    stage_total=sum(bool(turn.expect_stage) for result in results for turn in result.turns)
    stage_hits=sum(turn.expect_stage==turn.stage for result in results for turn in result.turns
                   if turn.expect_stage)
    remaining=70
    mock_calls=[]
    selected=[]
    for script in scripts:
        if not remaining:
            break
        partial=replace(script,turns=script.turns[:remaining])
        mock_calls.append(await run_text_call(partial))
        selected.append({'script':script.name,'turns':len(partial.turns)})
        remaining-=len(partial.turns)
    return {'kind':'mock_and_synthetic_only','scripted_turns':70-remaining,
            'selected_scripts':selected,'full_controller_replay_turns':sum(len(r.turns) for r in results),
            'controller_stage_accuracy':stage_hits/stage_total if stage_total else None,
            'controller_findings':sum(len(r.findings) for r in results),
            'canary_cases':len(CANARIES),'canary_failures':len(check_canaries()),
            'mock_model_requests':sum(r['model_requests'] for r in mock_calls),
            'mock_json_validity':sum(r['json_valid'] for r in mock_calls)/sum(r['json_total'] for r in mock_calls),
            'mock_ttft_ms':percentiles([v for r in mock_calls for v in r['mock_ttft_ms']]),
            'mock_provider_errors':sum(r['provider_errors'] for r in mock_calls),
            'unsafe_final_sentences':sum(r['unsafe_final_sentences'] for r in mock_calls),
            'native_input_tokens':None,'native_output_tokens':None,'native_semantic_quality':None,
            'limitations':['JSON/TTFT describe deterministic mock chunks, never real Claude quality or latency.',
                           'Full replay uses fixture reference replies and injected extraction, not database bookings.',
                           'No native models, private transcripts, paid API, real calls, audio or human scoring.']}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-offline',action='store_true',help='Run 70 public scripted mock turns and existing controller/canary suite')
    parser.add_argument('--run-chaos',action='store_true',help='Execute reviewed offline failure suites and capture outcome fingerprints')
    parser.add_argument('--allow-disposable-services',action='store_true',help='Include owned disposable SQL/Redis recovery and 100-request booking race tests')
    parser.add_argument('--evidence',type=Path,action='append',default=[],help='Metadata-only receipt envelope; never raw audio/transcripts')
    parser.add_argument('--output',type=Path)
    parser.add_argument('--require-full-gate',action='store_true')
    args=parser.parse_args(argv)
    if args.allow_disposable_services and not args.run_chaos:
        parser.error('--allow-disposable-services requires --run-chaos')
    receipts=[]
    for path in args.evidence:
        envelope,_=read_report(path)
        if set(envelope)!={'receipts'} or not isinstance(envelope['receipts'],list):
            parser.error('Evidence envelope requires only a receipts list')
        receipts.extend(envelope['receipts'])
    experiment=asyncio.run(offline_experiment()) if args.run_offline else None
    chaos=run_regressions(allow_native=args.allow_disposable_services) if args.run_chaos else None
    revision=subprocess.run(['git','rev-parse','HEAD'],capture_output=True,text=True,check=True).stdout.strip()
    identity={'revision':revision,'dataset_sha256':fingerprint([
        p.read_text() for p in sorted((ROOT/'evals/scripts').glob('*.jsonl'))]),
        'configuration_sha256':fingerprint(load_matrix()),'hardware':f'{platform.system()} {platform.machine()}',
        'conditions':'warm public fixture mock; working-tree source'}
    if experiment:
        receipts.append({'check_id':'llm.scripted_turns','kind':'mock','samples':experiment['scripted_turns'],
            'metrics':{'json_validity_rate':experiment['mock_json_validity'],
                       'ttft_p95_ms':experiment['mock_ttft_ms']['p95'],'input_tokens':None,'output_tokens':None},
            'identity':identity,'source':'scripts/evaluate_project.py --run-offline','approved':False})
        receipts.append({'check_id':'conversation.stage_accuracy','kind':'synthetic',
            'samples':experiment['full_controller_replay_turns'],
            'metrics':{'accuracy':experiment['controller_stage_accuracy']},'identity':identity,
            'source':'roma.eval.runner fixture extraction/reference replies','approved':False})
    if chaos:
        receipts.extend(regression_receipts(chaos))
    report=build_readiness(load_matrix(),receipts)
    report.update(identity=identity,offline_experiment=experiment,chaos_experiment=chaos,source_sha256={
        str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (
            ROOT/'roma/eval/project_readiness.py',ROOT/'roma/eval/backend_lab.py',
            ROOT/'scripts/evaluate_project.py',ROOT/'roma/eval/regressions.py',
            ROOT/'roma/services/streaming_voice.py')})
    text=json.dumps(report,indent=2,ensure_ascii=False,allow_nan=False)+'\n'
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(text)
    else:
        print(text,end='')
    failed=experiment and (experiment['controller_findings'] or experiment['canary_failures']
                          or experiment['mock_provider_errors'] or experiment['unsafe_final_sentences'])
    return 1 if failed or (chaos and not chaos['executed_suites_passed']) or (args.require_full_gate and not report['full_gate_passed']) else 0


if __name__=='__main__':
    raise SystemExit(main())
