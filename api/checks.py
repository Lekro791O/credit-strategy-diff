"""CHECK sources resolved without exec; all execution goes through checkrunner."""
import ast
from pathlib import Path
from checkrunner import CheckRunner, CheckSpec
from . import config, workspace
from .procedures import user_runner


def builtin_checks():
    out = []
    for path in sorted(config.BUILTIN_CHECKS_DIR.glob('check_*.py')):
        values = {}
        for node in ast.parse(path.read_text(encoding='utf8')).body:
            if not isinstance(node, ast.Assign) or not isinstance(node.targets[0], ast.Name):
                continue
            name = node.targets[0].id
            if name == 'CHECK':
                check = {}
                for key, value in zip(node.value.keys, node.value.values):
                    key = ast.literal_eval(key)
                    if key == 'entrypoint':
                        continue
                    check[key] = values[value.id] if isinstance(value, ast.Name) else ast.literal_eval(value)
                out.append(dict(id=check['id'], label=check['label'], params=check.get('params', {}), file=path.name))
            else:
                try:
                    values[name] = ast.literal_eval(node.value)
                except (ValueError, TypeError):
                    pass
    return out


def prepare(specs, user):
    """Freeze source once; a missing/bad source becomes its own execution error."""
    items = []
    builtin = builtin_checks()
    for spec in specs:
        if not spec.get('enabled', True):
            continue
        src = spec['source']
        item = dict(source=src, params=spec.get('params', {}), trusted=False)
        try:
            if src['kind'] == 'builtin':
                match = next((b for b in builtin if src['name'] in (b['id'], b['file'], Path(b['file']).stem)), None)
                if match is None:
                    raise ValueError('Unknown builtin check')
                path = config.BUILTIN_CHECKS_DIR / match['file']
                item.update(code=path.read_text(encoding='utf-8-sig'), origin='builtin:' + match['file'], trusted=True)
            else:
                code, origin = workspace.source_code(user, src)
                item.update(code=code, origin=origin)
        except Exception as exc:
            item['diagnostic'] = dict(error_type=type(exc).__name__, message=str(exc))
        items.append(item)
    return items


def execute(mart, items, *, on_event=None):
    combined = dict(rows=[], executions=[], status='COMPLETED', diagnostics=[])
    # Group adjacent builtins to share one snapshot. User code never inherits trust.
    groups = []
    for item in items:
        if groups and groups[-1][0]['trusted'] == item['trusted'] and not item.get('diagnostic') and not groups[-1][0].get('diagnostic'):
            groups[-1].append(item)
        else:
            groups.append([item])
    for group in groups:
        if group[0].get('diagnostic'):
            execution = dict(index=len(combined['executions']), status='ERROR', row_start=len(combined['rows']), row_count=0,
                             origin=str(group[0]['source']), diagnostic=group[0]['diagnostic'])
            combined['executions'].append(execution)
            if on_event: on_event(execution)
            continue
        runner = CheckRunner(backend='subprocess', trusted=True, timeout=120) if group[0]['trusted'] else user_runner()
        offset = len(combined['executions'])
        def event(e):
            if on_event: on_event(dict(e, index=e['index'] + offset))
        report = runner.run(mart._ensure_store(), mart.RUN_ID,
                            [CheckSpec(code=i['code'], name=i['origin'], params=i['params']) for i in group],
                            on_event=event, persist=False)
        for e in report['executions']:
            combined['executions'].append(dict(e, index=e['index'] + offset, row_start=e['row_start'] + len(combined['rows'])))
        combined['rows'].extend(report['rows'])
        combined['diagnostics'].extend(report['diagnostics'])
    errors = [e for e in combined['executions'] if e['status'] != 'COMPLETED']
    combined['status'] = 'COMPLETED_WITH_ERRORS' if errors else 'COMPLETED'
    combined['verdict'] = 'FAIL' if any(r['RESULT'] == 'FAIL' for r in combined['rows']) else None if errors or not combined['rows'] else 'PASS'
    return combined


def decorated_rows(report):
    return [dict(r, _check_id=e.get('check_id', '?'), _ref=e.get('origin', ''))
            for e in report['executions']
            for r in report['rows'][e['row_start']:e['row_start'] + e['row_count']]]
