"""Application-level pair eligibility, diagnostics and separate processing stages."""
from pathlib import Path
from collections import Counter
from .core import PipelineError
from .runner import Handlers, read_document
from .validation import validate
from .config import default_config


def dataset_directories(dataset_dir):
    root = Path(dataset_dir)
    for b, a in [(root/'RESULTS/BEFORE_MASTER', root/'RESULTS/AFTER_BETA'),
                 (root/'BEFORE_MASTER', root/'AFTER_BETA'), (root/'BEFORE', root/'AFTER')]:
        if b.is_dir() or a.is_dir():
            return b, a
    raise PipelineError('Dataset must contain RESULTS/BEFORE_MASTER + AFTER_BETA or BEFORE + AFTER')


def validate_response(document):
    if not isinstance(document, dict) or not document:
        raise PipelineError('Response must be a nonempty JSON object')
    decision = document.get('decisionResult')
    if not isinstance(decision, dict) or not isinstance(decision.get('decision'), str) or not decision['decision']:
        raise PipelineError('Response requires decisionResult.decision as a nonempty string')
    app = document.get('appData')
    if not isinstance(app, dict) or not isinstance(app.get('application_id'), (str, int)):
        raise PipelineError('Response requires appData.application_id')


def _detail(error):
    if not isinstance(error, PipelineError):
        return ''
    message = str(error)
    for prefix, code in [('Duplicate array key', 'DUPLICATE_ARRAY_KEY'),
                         ('Duplicate attribute', 'DUPLICATE_ATTRIBUTE'),
                         ('Missing key field', 'MISSING_ARRAY_KEY_FIELD'),
                         ('Array mode required', 'ARRAY_MODE_REQUIRED'),
                         ('APP_ID differs', 'APP_ID_MISMATCH'),
                         ('Invalid normalized', 'INVALID_NORMALIZED_ROWS'),
                         ('Response requires', 'REQUIRED_RESPONSE_FIELD')]:
        if message.startswith(prefix):
            return code
    return ''


def run_dataset(dataset_dir, *, config=None, handlers=None, event=None, validator=validate_response, expected_app_ids=None):
    """Only valid pairs enter comparison, segments and facts; failures are diagnostic.

    event receives stage/start|finish plus per-side progress. Exceptions in a global
    aggregate fail the run: they are not misrepresented as individual bad inputs.
    """
    h, config = handlers or Handlers(), config or default_config()
    before, after = dataset_directories(dataset_dir)
    files = [{p.stem: p for p in sorted(d.glob('*.json')) if p.is_file()} for d in (before, after)]
    expected_app_ids = list(expected_app_ids or [])
    if any(not isinstance(app, str) or not app for app in expected_app_ids):
        raise PipelineError('expected_app_ids must contain nonempty strings')
    ids = sorted(set(files[0]) | set(files[1]) | set(expected_app_ids))
    if not ids:
        raise PipelineError('No JSON applications found')
    diagnostics, normalized, documents, eligible = [], {}, {}, []
    def emit(stage, status, **kw):
        if event:
            if status == 'start':
                import inspect
                fn = getattr(h, 'aggregate' if stage == 'facts' else stage)
                kw['code_ref'] = getattr(fn, 'origin', getattr(fn, '__module__', '') + ':' + getattr(fn, '__name__', stage))
                try:
                    kw['code_snapshot'] = getattr(fn, 'code_snapshot', None) or inspect.getsource(fn)
                except (OSError, TypeError):
                    kw['code_snapshot'] = ''
            event({'stage': stage, 'status': status, **kw})
    def problem(app, source, stage, code, error=None):
        # No source values, source code or raw exception messages in public diagnostics.
        diagnostics.append({'APP_ID': app, 'SOURCE': source, 'STAGE': stage,
                            'CODE': code, 'ERROR_TYPE': type(error).__name__ if error else '',
                            'DETAIL_CODE': _detail(error),
                            'MESSAGE': {'MISSING_RESPONSE': 'Ответ отсутствует',
                                        'INVALID_JSON': 'Не удалось прочитать корректный JSON',
                                        'INVALID_RESPONSE': 'Ответ не прошёл структурную валидацию',
                                        'NORMALIZATION_FAILED': 'Ошибка нормализации или выходного контракта',
                                        'COMPARISON_FAILED': 'Ошибка сравнения или разрезов заявки'}[code]})
    emit('normalize', 'start', total=len(ids))
    for position, app in enumerate(ids, 1):
        valid = True
        for i, source in enumerate(('BEFORE', 'AFTER')):
            if app not in files[i]:
                problem(app, source, 'read', 'MISSING_RESPONSE'); valid = False; continue
            try:
                doc = read_document(files[i][app])
            except PipelineError as exc:
                problem(app, source, 'read', 'INVALID_JSON', exc); valid = False; continue
            try:
                validator(doc)
            except Exception as exc:
                problem(app, source, 'validate', 'INVALID_RESPONSE', exc); valid = False; continue
            try:
                rows = validate(list(h.normalize(doc, app_id=app, source=source, config=config)),
                                'normalized', app_id=app, source=source)
                if not rows:
                    raise PipelineError('Normalization produced no attributes')
            except Exception as exc:
                problem(app, source, 'normalize', 'NORMALIZATION_FAILED', exc); valid = False; continue
            normalized[app, source], documents[app, source] = rows, doc
        if valid:
            eligible.append(app)
        emit('normalize', 'progress', completed=position, total=len(ids))
    emit('normalize', 'finish', eligible=len(eligible))
    result = {k: [] for k in ('normalized_before', 'normalized_after', 'comparison', 'segments')}
    emit('compare', 'start', total=len(eligible))
    for app in eligible:
        try:
            b, a = normalized[app, 'BEFORE'], normalized[app, 'AFTER']
            comparison = validate(list(h.compare(b, a)), 'comparison', app_id=app)
            from .core import row_key
            if {row_key(r) for r in comparison} != {row_key(r) for r in b+a}:
                raise PipelineError('Comparison must retain the union of attribute keys')
            from .core import compare as reference_compare
            expected_rows = {row_key(r): r for r in reference_compare(b, a)}
            for row in comparison:
                expected = expected_rows[row_key(row)]
                if any(row[k] != expected[k] for k in ('VALUE_BEFORE', 'VALUE_AFTER', 'ADJUSTED_BEFORE', 'ADJUSTED_AFTER', 'DIFF_FLAG', 'CHANGE_TYPE')):
                    raise PipelineError('Comparison output contradicts input attributes')
                if row['CHANGE_TYPE'] in ('SAME', 'VALUE_CHANGED') and ((row['VALUE_BEFORE'] == row['VALUE_AFTER']) != (row['CHANGE_TYPE'] == 'SAME')):
                    raise PipelineError('Comparison values are not representable in mart contract')
            segments = validate([h.segment(documents[app, 'BEFORE'], documents[app, 'AFTER'], app_id=app)],
                                'segments', app_id=app)
            if segments[0]['DECISION_CHANGED'] != int(segments[0]['DECISION_BEFORE'] != segments[0]['DECISION_AFTER']):
                raise PipelineError('Segment decision flag contradicts decisions')
        except Exception as exc:
            problem(app, 'PAIR', 'compare', 'COMPARISON_FAILED', exc); continue
        result['comparison'].extend(comparison); result['segments'].extend(segments)
        result['normalized_before'].extend(b); result['normalized_after'].extend(a)
    emit('compare', 'finish', eligible=len(result['segments']))
    emit('facts', 'start')
    result['facts'] = validate(list(h.aggregate(result['comparison'])), 'facts')
    # A custom fact procedure may choose a subset of canonical groups, but cannot
    # invent counts or sever the link back to applications.
    from .core import aggregate, FACT_FIELDS
    grain = FACT_FIELDS[:-1]
    expected = {tuple(row[k] for k in grain): row['CNT'] for row in aggregate(result['comparison'])}
    seen = set()
    for fact in result['facts']:
        key = tuple(fact[k] for k in grain)
        if key in seen or key not in expected or fact['CNT'] != expected[key]:
            raise PipelineError('Fact procedure returned duplicate, unknown or inconsistent counts')
        seen.add(key)
    emit('facts', 'finish', facts=len(result['facts']))
    accepted = len(result['segments'])
    result['diagnostics'] = diagnostics
    result['quality'] = {'total_apps': len(ids), 'eligible_apps': accepted,
                         'excluded_apps': len(ids)-accepted,
                         'valid_before_responses': sum(s == 'BEFORE' for _, s in normalized),
                         'valid_after_responses': sum(s == 'AFTER' for _, s in normalized),
                         'issues_by_code': dict(sorted(Counter(d['CODE'] for d in diagnostics).items()))}
    return result
