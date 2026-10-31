"""Decide whether an hourly dispatch should also archive airport flight data."""
from datetime import datetime
from zoneinfo import ZoneInfo
import os
from pathlib import Path


def due(at, force=False):
    if at.tzinfo is None:
        raise ValueError('timezone-aware datetime required')
    return force or at.astimezone(ZoneInfo('Asia/Taipei')).hour in (0, 6, 12, 18)


if __name__ == '__main__':
    result = due(datetime.now(ZoneInfo('Asia/Taipei')),
                 os.getenv('FORCE_TAOYUAN', 'false').lower() == 'true')
    with Path(os.environ['GITHUB_OUTPUT']).open('a') as stream:
        stream.write('due=' + str(result).lower() + '\n')
    print('airport_snapshot_due=' + str(result).lower())
