import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys

from . import Handlers, PipelineError, default_config, load_handler, run_directories


def main(argv=None):
    parser = argparse.ArgumentParser(description='Normalize and compare paired SPR responses')
    parser.add_argument('--before', required=True, type=Path)
    parser.add_argument('--after', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path, help='New JSON artifact; never overwritten')
    parser.add_argument('--allow-unpaired', action='store_true')
    parser.add_argument('--config', help='Trusted module:factory returning Config')
    for stage in ('normalize', 'compare', 'aggregate', 'segment'):
        parser.add_argument('--' + stage, help='Trusted module:callable')
    args = parser.parse_args(argv)
    try:
        dest = args.output.resolve()
        if any(dest.is_relative_to(p.resolve()) for p in (args.before, args.after)):
            raise PipelineError('Output must be outside both input directories')
        if dest.exists():
            raise PipelineError('Output already exists; choose a new filename')
        h = Handlers()
        for stage in ('normalize', 'compare', 'aggregate', 'segment'):
            spec = getattr(args, stage)
            if spec:
                h = replace(h, **{stage: load_handler(spec)})
        config = load_handler(args.config)() if args.config else default_config()
        result = run_directories(args.before, args.after, config=config, handlers=h,
                                 allow_unpaired=args.allow_unpaired)
        with dest.open('x', encoding='utf-8') as file:
            json.dump(result, file, ensure_ascii=False, allow_nan=False)
        print(json.dumps({'applications': len(result['segments']),
                          'attributes': len(result['comparison']), 'facts': len(result['facts'])}))
        return 0
    except (PipelineError, OSError) as exc:
        print(f'pipeline: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
