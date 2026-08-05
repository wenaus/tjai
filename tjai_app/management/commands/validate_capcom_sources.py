import importlib
import sys
from collections import Counter
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from tjai_app import capcom


class Command(BaseCommand):
    help = 'Validate that every CAPCOM state tile has a complete source definition.'

    def handle(self, *args, **options):
        scripts_dir = Path(settings.BASE_DIR) / 'scripts'
        sys.path.insert(0, str(scripts_dir))
        try:
            dispatcher = importlib.import_module('capcom_dispatcher')
        except Exception as exc:
            raise CommandError(
                f'CAPCOM dispatcher could not be loaded: {exc}') from exc
        finally:
            sys.path.remove(str(scripts_dir))

        errors = []
        sources = capcom.get_sources()
        state = capcom.get_state()
        if not isinstance(sources, list):
            raise CommandError('capcom_sources is not a list')
        if not isinstance(state, dict):
            raise CommandError('capcom_state is not an object')

        state_rows = [
            row for row in sources
            if isinstance(row, dict) and row.get('kind') == 'state'
        ]
        state_names = [row.get('source') for row in state_rows]
        duplicate_names = sorted(
            name for name, count in Counter(state_names).items()
            if name and count > 1
        )
        if duplicate_names:
            errors.append(
                'duplicate state source rows: ' + ', '.join(duplicate_names))

        registered = {name for name in state_names if name}
        missing = sorted(set(state) - registered)
        if missing:
            errors.append(
                'state tiles missing source rows: ' + ', '.join(missing))

        collector_names = set(dispatcher.COLLECTORS)
        for row in state_rows:
            source = row.get('source')
            if not source:
                errors.append('state source row has no source name')
                continue
            mode = row.get('mode')
            if mode not in {'poll', 'listen', 'missing'}:
                errors.append(f'{source}: invalid or missing mode {mode!r}')
                continue
            if mode == 'poll':
                collector = row.get('collector') or source
                if collector not in collector_names:
                    errors.append(
                        f'{source}: collector {collector!r} is not implemented')

        if errors:
            raise CommandError(
                'CAPCOM state-source validation failed:\n- ' + '\n- '.join(errors))
        self.stdout.write(self.style.SUCCESS(
            f'CAPCOM state sources valid ({len(state)} tiles)'))
