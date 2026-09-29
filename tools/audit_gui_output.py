#!/usr/bin/env python3
"""Independently replay exported GUI-run motions and finite stock/fit probes."""
import argparse
from copy import deepcopy
from dataclasses import asdict
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tests'))
from oracles.gcode import parse_program
from oracles.motion import (Cutter,apply_affine,assert_air_travel,assert_fixture_reach,
                            assert_probe_stepdown,assert_sweep_bounds,check_result_contract,
                            operations_removal)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run',type=Path)
    options=parser.parse_args()
    run=options.run.resolve()
    started=time.monotonic()
    report=json.loads((run/'report.json').read_text())
    spec=json.loads((run/'spec.json').read_text())
    plan=json.loads((run/'plan.json').read_text())
    errors=check_result_contract(spec,plan)
    if not report.get('ok') or report.get('exit_code')!=0:
        errors.append('GUI process did not complete successfully')
    tools={tool['id']:tool for tool in spec['tools']}
    cutters={key:Cutter.from_snapshot(tool) for key,tool in tools.items() if tool['id'] in spec['selected_tool_ids']}
    artifact_names=['report.json','spec.json','plan.json','paired-inlay.FCStd','board.ngc','plug.ngc']
    files={name:sha(run/name) for name in artifact_names}
    exported={}
    # Preserve operation intent/identity, but reconstruct the geometry tested
    # below exclusively from independently parsed controller coordinates.
    actual_plan=deepcopy(plan)
    actual_operations={op['id']:op for op in actual_plan['operations']}
    for op in actual_operations.values():op['moves']=[]
    for face in ('board','plug'):
        operations=[op for op in plan['operations'] if op['face']==face]
        program=parse_program((run/(face+'.ngc')).read_text())
        expected=[]
        for op in operations:
            first=op['moves'][0]
            # A separate native Z-only establishment is permitted before each
            # operation's exact planner sequence; initial XY must remain unknown.
            expected.append((op,first,True))
            expected.extend((op,move,False) for move in op['moves'])
        if len(program.motions)!=len(expected):
            errors.append(face+': exported motion count differs from accepted planner sequence')
        prior_position=(None,None,None)
        tolerance=Decimal(str(min(spec['resolution'].values())*.01))
        for index,(motion,(op,move,approach)) in enumerate(zip(program.motions,expected)):
            for axis,actual in zip('xyz',motion.end):
                if approach and axis in 'xy':
                    wanted=prior_position['xyz'.index(axis)]
                else:
                    wanted=Decimal(str(move[axis]))
                if actual is None or wanted is None:
                    equal=actual==wanted
                else:
                    equal=abs(actual-wanted)<=tolerance
                if not equal:
                    errors.append(f'{face} motion{index}: exported {axis.upper()} differs from accepted motion')
            if motion.kind!=move['kind']:
                errors.append(f'{face} motion{index}: motion kind differs')
            if motion.kind=='feed' and (motion.feed_mm_min is None or abs(motion.feed_mm_min-Decimal(str(move['feed_mm_min'])))>Decimal('0.000001')):
                errors.append(f'{face} motion{index}: feed differs')
            if motion.tool!=tools[op['tool_id']]['number'] or motion.length_offset!=motion.tool:
                errors.append(f'{face} motion{index}: physical tool or length compensation differs')
            if motion.spindle_mode!='M3' or motion.spindle_rpm!=Decimal(str(op['settings']['spindle'])):
                errors.append(f'{face} motion{index}: spindle differs')
            if motion.path_control!='G61' or motion.work_offset!='G54':
                errors.append(f'{face} motion{index}: path control or work offset differs')
            if not approach and all(value is not None for value in motion.end):
                actual=dict(move,kind=motion.kind,**dict(zip('xyz',map(float,motion.end))))
                if motion.feed_mm_min is not None:actual['feed_mm_min']=float(motion.feed_mm_min)
                actual_operations[op['id']]['moves'].append(actual)
            prior_position=motion.end
        if program.tool_changes!=plan['per_face_counts'][face]['tool_changes']:
            errors.append(face+': exported tool changes differ')
        exported[face]={'moves':len(program.motions),'tool_changes':program.tool_changes}
        setup=spec['faces'][face]
        for planned in operations:
            op=actual_operations[planned['id']]
            try:
                assert_air_travel(op['moves'],setup['origin'])
                assert_fixture_reach(op['moves'],cutters[op['tool_id']],setup['stock'][2],setup['origin'])
                assert_sweep_bounds(op['moves'],cutters[op['tool_id']],setup['bounds'],setup['origin'],
                                    stock_bounds=(0,0,*setup['stock'][:2]))
            except AssertionError as error:
                errors.append(op['id']+': '+str(error))
    # Probe in canonical coordinates over the included artwork bounds and its
    # neighbouring backing. Multiple cutter uses retain their actual order.
    vertices=[p for region in spec['artwork']['regions'] for poly in region['polygons'] for p in poly['outer']]
    x0,x1=min(p[0] for p in vertices),max(p[0] for p in vertices)
    y0,y1=min(p[1] for p in vertices),max(p[1] for p in vertices)
    padx,pady=(x1-x0)*.25,(y1-y0)*.25
    points=[(x0-padx+(x1-x0+2*padx)*i/6,y0-pady+(y1-y0+2*pady)*j/6) for i in range(7) for j in range(7)]
    errors.extend('Exported contract: '+error for error in check_result_contract(spec,actual_plan))
    operations={face:[op for op in actual_plan['operations'] if op['face']==face] for face in ('board','plug')}
    if any('stock_outline' in setup for setup in spec['faces'].values()):
        errors.append('This independent GUI probe audit requires rectangular stocks; use the captured-stock oracle for shaped stock')
    for face in ('board','plug'):
        setup=spec['faces'][face]
        mapped=[apply_affine(point,setup['design_to_face']) for point in points]
        try:
            assert_probe_stepdown(mapped,operations[face],cutters,setup['origin'])
        except AssertionError as error:
            errors.append(face+': '+str(error))
    probes=[]
    for point in points:
        intervals={}
        for face in ('board','plug'):
            setup=spec['faces'][face]
            mapped=apply_affine(point,setup['design_to_face'])
            if not(0<=mapped[0]<=setup['stock'][0] and 0<=mapped[1]<=setup['stock'][1]):
                break
            intervals[face]=operations_removal(mapped,operations[face],cutters,setup['origin'])
        if len(intervals)!=2: continue
        low=sum(interval.lower for interval in intervals.values())-plan['derived']['seating_depth']
        high=sum(interval.upper for interval in intervals.values())-plan['derived']['seating_depth']
        certified=all(interval.certified for interval in intervals.values())
        probes.append({'canonical_xy':point,'gap_lower_mm':low,'gap_upper_mm':high,'certified':certified})
        if not certified or low < -1e-6:
            errors.append('Unresolved/interfering assembly probe at '+str(point))
    source_paths=[Path(__file__),ROOT/'tests/oracles/gcode.py',ROOT/'tests/oracles/motion.py']
    result={'schema_version':1,'gui_run':report['run_id'],'archive_sha256':report['archive_sha256'],
        'passed':not errors,'errors':errors,'elapsed_s':time.monotonic()-started,'exported':exported,'probes':probes,
        'artifact_sha256':files,'oracle_source_sha256':{str(p.relative_to(ROOT)):sha(p) for p in source_paths},
        'geometry_source':'Coordinates and feed values reconstructed from independently parsed exported G-code; operation grouping and intended cutting/travel roles are matched to the accepted sequence. Native clearance-only Z establishments are separately checked.',
        'scope':'Actual exported G-code versus accepted planner coordinates, feeds, tools, counts, modal controls; independent continuous-segment cutter bounds and finite engagement/assembly probes run on reconstructed exported motion. Finite probes do not replace production whole-stock verification or physical cuts.'}
    target=run/'independent-audit.json'
    target.write_text(json.dumps(result,indent=2))
    print(json.dumps({'passed':result['passed'],'errors':errors[:20],'evidence':str(target),'elapsed_s':result['elapsed_s']}))
    raise SystemExit(0 if result['passed'] else 1)


if __name__=='__main__':main()
