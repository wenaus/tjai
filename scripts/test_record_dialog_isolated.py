"""Isolated, synthetic checks for the dialog REST/MCP shared ingestion change.

Run with the repository on PYTHONPATH. Settings are configured here before any
project import; no .env, production settings, credentials or database are read.
"""
import ast
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import uuid
from datetime import datetime
from unittest.mock import patch

os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
from django.conf import settings

scratch = tempfile.TemporaryDirectory(prefix='tjai-dialog-isolated-')
settings.configure(
    SECRET_KEY='synthetic-test-secret',
    INSTALLED_APPS=['django.contrib.auth', 'django.contrib.contenttypes', 'tjai_app'],
    DATABASES={'default': {'ENGINE': 'django.db.backends.sqlite3',
                           'NAME': str(Path(scratch.name) / 'synthetic.sqlite3')}},
    ALLOWED_HOSTS=['testserver', 'localhost'], USE_TZ=True, TIME_ZONE='UTC',
    TJAI_MCP_SERVER_CONFIG={'name': 'synthetic-tjai', 'instructions': 'Synthetic test only'},
    DEFAULT_AUTO_FIELD='django.db.models.BigAutoField',
)
import django
django.setup()
from django.contrib.auth.models import User
from django.db import connection, connections, transaction
from django.test import RequestFactory
from django.utils import timezone
from starlette.testclient import TestClient
from tjai_app import dialog, mcp, oauth, views
from tjai_app.models import Context, Entry, Tag, SysConfig
from tjai_app.oauth_models import OAuthClient, OAuthGrant, OAuthToken
from tjai_project.mcp_asgi import application

with connection.schema_editor() as editor:
    for model in (User, Context, Entry, Tag, SysConfig, OAuthClient, OAuthGrant, OAuthToken):
        editor.create_model(model)

SysConfig.objects.create(key='gmail_addon_api_key', value='synthetic-rest', timestamp_modified=time.time())
SysConfig.objects.create(key='mcp_bearer_token', value='synthetic-bearer', timestamp_modified=time.time())
client = OAuthClient.objects.create(client_id='synthetic-client', info={}, created_at=timezone.now())
grant = OAuthGrant.objects.create(id='synthetic-grant', client=client, params={}, created_at=timezone.now())
for token, overrides in (
    ('synthetic-access', {}),
    ('synthetic-expired', {'expires_at': time.time() - 10}),
    ('synthetic-revoked', {'revoked': True}),
    ('synthetic-resource', {'resource': 'https://example.invalid/other'}),
    ('synthetic-scope', {'scopes': ['other']}),
    ('synthetic-refresh', {'kind': 'refresh'}),
):
    OAuthToken.objects.create(**{
        'token_hash': oauth._hash(token), 'kind': 'access', 'grant': grant,
        'scopes': ['tjai'], 'resource': oauth.RESOURCE, 'expires_at': time.time() + 3600,
        'created_at': timezone.now(), **overrides,
    })

factory = RequestFactory()
base = dict(content='  Synthetic visible message.  ', role='assistant', client='chatgpt',
            hostname='synthetic-cloud', session_id='synthetic-session', source_id='message-1',
            timestamp='2026-10-10T04:30:12.345-05:00')

def post(payload, function=views.api_dialog, auth='synthetic-rest', raw=False):
    response = function(factory.post('/tjai/api/dialog',
        data=payload if raw else json.dumps(payload), content_type='application/json',
        **({} if auth is None else {'HTTP_AUTHORIZATION': f'Bearer {auth}'})))
    return response.status_code, json.loads(response.content)

def snapshot(payload, function):
    with transaction.atomic():
        status, result = post(payload, function)
        assert status == 200, result
        entry = Entry.objects.get(pk=result['entry_id'])
        snap = {key: getattr(entry, key) for key in (
            'id', 'content', 'kind', 'context_id', 'timestamp_created',
            'timestamp_modified', 'is_dirty', 'data')}
        snap['tags'] = sorted(Tag.objects.filter(entry=entry).values_list('tag_name', flat=True))
        transaction.set_rollback(True)
    return snap

# Compare the extracted code with the original endpoint on the inspected base.
repo = Path(views.__file__).resolve().parents[1]
original = subprocess.check_output(['git', 'show', 'c5090b6:tjai_app/views.py'], cwd=repo, text=True)
node = next(n for n in ast.parse(original).body if isinstance(n, ast.FunctionDef) and n.name == 'api_dialog')
namespace = dict(vars(views))
exec(compile(ast.Module(body=[node], type_ignores=[]), '<baseline-api-dialog>', 'exec'), namespace)
baseline = namespace['api_dialog']
source = Entry.objects.create(id=str(uuid.uuid4()), content='Synthetic research source', kind='action',
    data={'entry_id': 'synthetic-research'}, timestamp_created=time.time(), timestamp_modified=time.time())
SysConfig.objects.create(key='agent_research-agent_entry', value=source.id, timestamp_modified=time.time())
variants = [base, dict(base, role='user'),
    dict(base, client='claude-code', content_type='thinking', model='synthetic-model',
         model_provider='synthetic-provider', reasoning_effort='high', project_path='/synthetic/project'),
    {k: v for k, v in base.items() if k not in ('source_id', 'timestamp')},
    dict(base, content='<task-notification><summary>Agent sample completed</summary></task-notification>'),
    *[dict(base, source_id=value) for value in (None, False, 0, [], {}, "", " ")],
    dict(base, session_id=' '), dict(base, client=123), dict(base, model=[]),
    dict(base, content_type={'legacy': 'metadata'})]
with patch('time.time', return_value=1791624612.0), patch('uuid.uuid7', return_value=uuid.UUID(int=1)):
    for payload in variants:
        assert snapshot(payload, baseline) == snapshot(payload, views.api_dialog)
print('PASS: exact baseline parity for normal, user, hook metadata, legacy edge cases and research attribution writes')

before = Entry.objects.count()
def rest_outcome(payload, function):
    with transaction.atomic():
        try:
            outcome = ('response', post(payload, function))
        except (AttributeError, TypeError, ValueError) as exc:
            outcome = ('exception', type(exc).__name__, str(exc))
        transaction.set_rollback(True)
    return outcome

invalids = [[], None, 'text', {}, dict(base, content=None), dict(base, content=' '),
    dict(base, content={}), dict(base, role='tool'), dict(base, role=1),
    dict(base, source_id=['nonempty']), dict(base, source_id=1),
    dict(base, source_id='x' * 513), dict(base, hostname={}), dict(base, client=''),
    dict(base, timestamp='2026-10-10T09:30:12'), dict(base, timestamp='bad'),
    dict(base, timestamp=123)]
for payload in invalids:
    assert rest_outcome(payload, baseline) == rest_outcome(payload, views.api_dialog), payload
with patch.object(Entry.objects, 'get_or_create', side_effect=ValueError('synthetic storage failure')):
    assert rest_outcome(base, baseline) == rest_outcome(base, views.api_dialog)
    assert rest_outcome(base, views.api_dialog)[0] == 'exception'
assert post('{', raw=True)[0] == 400
assert post(base, auth=None)[0] == 401
assert post(base, auth='wrong')[0] == 403
assert Entry.objects.count() == before
print('PASS: exact REST malformed-input responses/exceptions and bearer auth; storage errors remain exceptions')

with patch('tjai_app.comms_dialog.recorded_native_peer') as peer:
    peer.return_value.id = 'synthetic-peer-entry'
    result = dialog.record_dialog(dict(base, role='user'))
    assert result['entry_id'] == 'synthetic-peer-entry'
    assert peer.call_args.args[2] == datetime.fromisoformat(base['timestamp']).timestamp()
assert Entry.objects.count() == before

def rpc(http, method, params=None, token='synthetic-access', path='/tjai/mcp-oauth'):
    headers = {'Accept': 'application/json, text/event-stream'}
    if token is not None:
        headers['Authorization'] = 'Bearer ' + token
    return http.post(path, headers=headers,
        json={'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params or {}})

def call(http, payload, **kwargs):
    response = rpc(http, 'tools/call', {'name': 'record_dialog', 'arguments': payload}, **kwargs)
    assert response.status_code == 200, response.text
    result = response.json()['result']
    if result.get('isError'):
        return {'error': result['content'][0]['text']}
    return result.get('structuredContent') or json.loads(result['content'][0]['text'])

with TestClient(application) as http:
    listed = rpc(http, 'tools/list').json()['result']['tools']
    tool = next(t for t in listed if t['name'] == 'record_dialog')
    schema = tool['inputSchema']
    assert set(schema['required']) == set(base)
    assert schema['properties']['role']['enum'] == ['user', 'assistant']
    assert schema['properties']['source_id']['maxLength'] == 512
    assert 'content_type' not in schema['properties']
    assert tool['annotations']['idempotentHint'] is True
    other_tools = rpc(http, 'tools/list', token='synthetic-bearer', path='/tjai/mcp/').json()['result']['tools']
    assert tool == next(t for t in other_tools if t['name'] == 'record_dialog')

    for token in (None, 'wrong', 'synthetic-bearer', 'synthetic-expired', 'synthetic-revoked',
                  'synthetic-resource', 'synthetic-scope', 'synthetic-refresh'):
        response = rpc(http, 'tools/call', {'name': 'record_dialog', 'arguments': base}, token=token)
        assert response.status_code == 401, response.text
    assert rpc(http, 'tools/list', token='synthetic-access', path='/tjai/mcp/').status_code == 403
    assert rpc(http, 'tools/list', token=None, path='/tjai/mcp/').status_code == 401
    assert Entry.objects.count() == before
    print('PASS: registered schema on both endpoints; OAuth expiry/revocation/resource/scope and bearer isolation')

    first = call(http, base)
    assert first['status'] == 'ok', first
    entry = Entry.objects.get(pk=first['entry_id'])
    created, modified = entry.timestamp_created, entry.timestamp_modified
    expected_id = str(uuid.uuid5(uuid.NAMESPACE_URL, json.dumps([
        'tjai-dialog-v1', base['hostname'], base['client'], base['session_id'], base['role'], base['source_id']])))
    assert entry.id == expected_id
    assert created == datetime.fromisoformat(base['timestamp']).timestamp()
    assert entry.content == base['content'].strip() and entry.data['content_type'] == 'text'
    assert entry.data['source_timestamp'] == base['timestamp']
    assert entry.kind == 'memory' and entry.is_dirty == 0 and entry.context_id == 'co-code'
    assert call(http, dict(base, content='Synthetic changed retry', timestamp='2026-10-10T12:00:00Z')) == first
    assert post(base)[1] == first
    assert call(http, base, token='synthetic-bearer', path='/tjai/mcp/') == first
    entry.refresh_from_db()
    assert entry.content == base['content'].strip()
    assert (entry.timestamp_created, entry.timestamp_modified) == (created, modified)
    assert Tag.objects.filter(entry=entry, tag_name='ccdialog').count() == 1
    assert Entry.objects.count() == before + 1
    for field in ('source_id', 'session_id', 'hostname', 'client', 'role'):
        value = 'user' if field == 'role' else 'different-' + field
        assert call(http, dict(base, **{field: value}))['entry_id'] != first['entry_id']
    assert call(http, dict(base, source_id='equal-instant', timestamp='2026-10-10T09:30:12.345Z'))['status'] == 'ok'
    legacy = {'content': 'Synthetic legacy', 'role': 'assistant', 'client': 'codex'}
    assert post(legacy)[1]['entry_id'] != post(legacy)[1]['entry_id']
    print('PASS: preserved timestamps/metadata/context/tags; cross-transport retry identity; distinct turns and legacy IDs')

    before_invalid = Entry.objects.count()
    for fields in ({'role': 'system'}, {'role': 'tool'}, {'role': 'developer'}, {'content': ' '},
                   {'timestamp': 'bad'}, {'timestamp': None}, {'source_id': ''}, {'source_id': ' '},
                   {'hostname': ' '}, {'session_id': ''}, {'source_id': 'x' * 513}, {'client': []},
                   {'source_id': 0}, {'source_id': False}, {'source_id': []}, {'model': []},
                   {'content': None}, {'role': 1}, {'hostname': {}}):
        assert 'error' in call(http, dict(base, **fields)), fields
    for key in ('source_id', 'timestamp'):
        assert 'error' in call(http, {k: v for k, v in base.items() if k != key})
    assert Entry.objects.count() == before_invalid
    print('PASS: MCP role, identity, timestamp and content validation rejects invalid requests without writes')

connections.close_all()
scratch.cleanup()
print('PASS: synthetic temporary SQLite database only; no production settings, credentials or writes')
