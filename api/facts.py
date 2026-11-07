"""SQL vertical facts: COUNT(DISTINCT APP_ID), independent group and grand totals."""
import json
from pipeline.pivot import SEGMENT_DIMS, segment_matches

CHANGE_TYPE_RU = {'SAME': 'без изменений', 'VALUE_CHANGED': 'значение изменено',
                  'ATTRIBUTE_NEW': 'атрибут появился', 'ATTRIBUTE_LOST': 'атрибут исчез',
                  'ADDED': 'атрибут появился', 'REMOVED': 'атрибут исчез'}

def change_type_ru(value):
    return CHANGE_TYPE_RU.get(value, value)

DIMS = {k.lower(): k for k in ['PREFIX', 'CLASS', 'SUFFIX', 'KEY', 'PATH', 'ARRAY_KEY',
        'SOURCE', 'VALUE_ADJUSTED', 'VALUE', 'CHANGE_TYPE', 'VALUE_BEFORE', 'VALUE_AFTER',
        'ADJUSTED_BEFORE', 'ADJUSTED_AFTER', 'DIFF_FLAG', *SEGMENT_DIMS]}
# алиасы подписок: короткие имена, которые шлёт фронтенд
DIMS.update({'bucket': 'VALUE_ADJUSTED', 'value': 'VALUE'})

PRODUCT_DIMS = {'product': 'PRODUCT', 'partner_id': 'PRODUCT_PARTNER_ID',
                'partner_name': 'PRODUCT_PARTNER_NAME', 'partner_type': 'PRODUCT_PARTNER_TYPE',
                'routing_method': 'PRODUCT_ROUTING_METHOD',
                'pd_model_before': 'PRODUCT_PD_MODEL_BEFORE', 'pd_model_after': 'PRODUCT_PD_MODEL_AFTER',
                'cutoff_before': 'PRODUCT_CUTOFF_BEFORE', 'cutoff_after': 'PRODUCT_CUTOFF_AFTER'}
DIMS.update(PRODUCT_DIMS)


def _vertical(mart):
    if mart.fact_table == 'comparison':
        # Existing participant-2 membership view is based on stored vertical datamart.
        return ("SELECT f.*, c.VALUE_BEFORE, c.VALUE_AFTER, c.ADJUSTED_BEFORE, c.ADJUSTED_AFTER "
                "FROM fact_members f JOIN comparison c USING(run_id, APP_ID, PATH, ARRAY_KEY) "
                f"WHERE f.{mart.run_clause}")
    # SQLite fallback reconstructs only the present sides; no artificial missing facts.
    return ("SELECT c.*, 'BEFORE (MASTER)' AS SOURCE, ADJUSTED_BEFORE AS VALUE_ADJUSTED "
            "FROM mart c WHERE CHANGE_TYPE != 'ATTRIBUTE_NEW' UNION ALL "
            "SELECT c.*, 'AFTER (BETA)' AS SOURCE, ADJUSTED_AFTER AS VALUE_ADJUSTED "
            "FROM mart c WHERE CHANGE_TYPE != 'ATTRIBUTE_LOST'")


def _selected(mart, filters, segment_filters, segment_rows=None, fact_scope=None, expand_products=False):
    filters = filters or {}
    allowed = set(DIMS) | {'path_prefix', 'app_ids'} | {'partner', 'routing', 'pd_model', 'cutoff'}
    if set(filters)-allowed:
        raise ValueError('Unknown fact filter')
    columns = ', '.join(f's."{k}"' for k in SEGMENT_DIMS)
    join = 'v.APP_ID = s.APP_ID'
    if mart.run_clause:
        join += f' AND s.{mart.run_clause}'
    # Product dimensions are memberships, not a random first product or a JSON map.
    expand_products = expand_products or bool(set(filters) & set(PRODUCT_DIMS))
    params, extra_cols, extra_join = [], '', ''
    if expand_products:
        segments = segment_rows if segment_rows is not None else list(mart.segments())
        memberships = []
        for seg in segments:
            b, a = seg.get('PRODUCT_DETAILS_BEFORE', {}), seg.get('PRODUCT_DETAILS_AFTER', {})
            products = sorted(set(filter(None, seg.get('PRODUCTS', '').split(';')))) or ['']
            for product in products:
                one = dict(seg, PRODUCTS=product,
                           PRODUCT_DETAILS_BEFORE={product: b[product]} if product in b else {},
                           PRODUCT_DETAILS_AFTER={product: a[product]} if product in a else {})
                if not segment_matches(one, segment_filters or {}):
                    continue
                row = {'APP_ID': seg['APP_ID'], 'PRODUCT': product}
                for key, col in PRODUCT_DIMS.items():
                    if key == 'product':
                        continue
                    field = key.upper()
                    if field.endswith('_BEFORE') or field.endswith('_AFTER'):
                        name, source = field.rsplit('_', 1)
                        value = (b if source == 'BEFORE' else a).get(product, {}).get(name)
                    else:
                        value = a.get(product, {}).get(field, b.get(product, {}).get(field))
                    if value is None:
                        # Mandatory legacy fields remain a fallback for older persisted runs.
                        value = seg.get(field, '') if not b and not a else ''
                    row[col] = value
                memberships.append(row)
        extract = 'json_extract_string' if mart.fact_table == 'comparison' else 'json_extract'
        extra_cols = ', ' + ', '.join(f"{extract}(p.value, '$.{col}') AS \"{col}\"" for col in PRODUCT_DIMS.values())
        extra_join = f" JOIN json_each(?) p ON v.APP_ID = {extract}(p.value, '$.APP_ID')"
        params.append(json.dumps(memberships))
    relation = f'WITH vertical AS ({_vertical(mart)}), joined AS (SELECT v.*, {columns}{extra_cols} FROM vertical v JOIN segments s ON {join}{extra_join}), selected AS (SELECT * FROM joined WHERE '
    clauses = []
    for key, value in filters.items():
        values = value if isinstance(value, list) else [value]
        if any(type(v) not in (str, int) for v in values):
            raise ValueError('Fact filters require strings/integers or lists of them')
        if key == 'path_prefix':
            if not isinstance(value, str):
                raise ValueError('path_prefix must be a string')
            clauses.append('substr(PATH, 1, ?) = ?'); params.extend([len(value), value]); continue
        if key in ('pd_model', 'cutoff'):
            # фильтр-алиас фронтенда: значение на любой из сторон (до/после)
            col = 'PD_MODEL' if key == 'pd_model' else 'CUTOFF'
            marks = ','.join('?' for _ in values) if values else 'NULL'
            clauses.append(f'("{col}_BEFORE" IN ({marks}) OR "{col}_AFTER" IN ({marks}))')
            params.extend(values); params.extend(values); continue
        if key in ('partner', 'routing'):
            # фильтр-алиасы фронтенда → колонки слоя сегментов (не продуктовые)
            col = {'partner': 'PARTNER_TYPE', 'routing': 'ROUTING_METHOD'}[key]
            clauses.append(f'"{col}" = ?'); params.append(value); continue
        col = 'APP_ID' if key == 'app_ids' else DIMS[key]
        if key == 'source':
            convert = lambda v: {'BEFORE': 'BEFORE (MASTER)', 'AFTER': 'AFTER (BETA)'}.get(v, v)
            value = [convert(v) for v in value] if isinstance(value, list) else convert(value)
        if isinstance(value, list):
            clauses.append(f'"{col}" IN ({",".join("?" for _ in value)})' if value else '1=0')
            params.extend(value)
        else:
            clauses.append(f'"{col}" = ?'); params.append(value)
    if segment_filters:
        segments = segment_rows if segment_rows is not None else list(mart.segments())
        ids = [s['APP_ID'] for s in segments if segment_matches(s, segment_filters)]
        clauses.append(f'APP_ID IN ({",".join("?" for _ in ids)})' if ids else '1=0')
        params.extend(ids)
    if fact_scope is not None:
        scope = [dict(row, SOURCE={'BEFORE':'BEFORE (MASTER)', 'AFTER':'AFTER (BETA)'}.get(row['SOURCE'], row['SOURCE'])) for row in fact_scope]
        extract = 'json_extract_string' if mart.fact_table == 'comparison' else 'json_extract'
        grain = ['PREFIX', 'CLASS', 'SUFFIX', 'ARRAY_KEY', 'KEY', 'VALUE_ADJUSTED', 'DIFF_FLAG', 'SOURCE']
        tests = []
        for field in grain:
            value = f"{extract}(j.value, '$.{field}')"
            if field == 'DIFF_FLAG':
                value = f'CAST({value} AS INTEGER)'
            tests.append(f'joined."{field}" = {value}')
        clauses.append('EXISTS (SELECT 1 FROM json_each(?) j WHERE ' + ' AND '.join(tests) + ')')
        params.append(json.dumps(scope))
    return relation + (' AND '.join(clauses) if clauses else '1=1') + ') ', params


def _group(mart, cte, params, dims, limit=None, offset=0):
    cols = [f'"{DIMS[d]}"' for d in dims]
    selected = ', '.join(f'{c} AS d{i}' for i, c in enumerate(cols))
    identities = list(dict.fromkeys(cols + ['"APP_ID"', '"PATH"', '"ARRAY_KEY"', '"SOURCE"']))
    base = '(SELECT DISTINCT ' + ', '.join(identities) + ' FROM selected) AS unique_members'
    sql = cte + f'SELECT {selected + "," if selected else ""} COUNT(*) AS cnt_rows, COUNT(DISTINCT APP_ID) AS cnt_apps FROM {base}'
    if cols:
        sql += ' GROUP BY ' + ','.join(cols)
    sql += ' ORDER BY cnt_apps DESC' + (', ' + ','.join(cols) if cols else '')
    args = list(params)
    if limit is not None:
        sql += ' LIMIT ? OFFSET ?'; args.extend([limit, offset])
    return [{'dims': {d: str(r[f'd{i}']) if r[f'd{i}'] is not None else '' for i, d in enumerate(dims)},
             'cnt_rows': r['cnt_rows'], 'cnt_apps': r['cnt_apps']} for r in mart.query(sql, args)]


def fact_result(mart, dims, filters=None, *, segment_filters=None, segment_rows=None,
                column_dimension=None, limit=5000, offset=0, fact_scope=None):
    if not dims or len(set(dims)) != len(dims) or set(dims)-set(DIMS):
        raise ValueError('Unknown, empty or duplicate fact dimensions')
    if column_dimension is not None and column_dimension not in dims:
        raise ValueError('Column dimension must belong to dimensions')
    if not 1 <= limit <= 10000 or offset < 0:
        raise ValueError('Invalid fact page')
    cte, params = _selected(mart, filters, segment_filters, segment_rows, fact_scope,
                            expand_products=bool(set(dims) & set(PRODUCT_DIMS)))
    rows = _group(mart, cte, params, dims, limit, offset)
    totals = _group(mart, cte, params, [])[0]
    row_dims = [d for d in dims if d != column_dimension]
    row_totals = _group(mart, cte, params, row_dims)
    column_totals = _group(mart, cte, params, [column_dimension]) if column_dimension else []
    return {'rows': rows, 'totals': totals, 'row_totals': row_totals,
            'column_totals': column_totals, 'limit': limit, 'offset': offset}


def fact_rows(mart, dims, filters=None, limit=5000):
    return fact_result(mart, dims, filters, limit=limit)['rows']


def apps_in_group(mart, dims, filters=None, offset=0, limit=200, *, segment_filters=None, segment_rows=None, fact_scope=None):
    if not 1 <= limit <= 10000 or offset < 0:
        raise ValueError('Invalid app page')
    cte, params = _selected(mart, {**(filters or {}), **dims}, segment_filters, segment_rows, fact_scope)
    rows = mart.query(cte + 'SELECT DISTINCT APP_ID FROM selected ORDER BY APP_ID LIMIT ? OFFSET ?', params+[limit, offset])
    total = mart.query(cte + 'SELECT COUNT(DISTINCT APP_ID) AS n FROM selected', params)[0]['n']
    return {'app_ids': [r['APP_ID'] for r in rows], 'total': total}
