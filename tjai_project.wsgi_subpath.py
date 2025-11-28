# WSGI entrypoint for subpath deployment at /tjai, using production settings
import os
from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'tjai_project.settings.production')

class ScriptNameFix:
    def __init__(self, app, script_name):
        self.app = app
        self.script_name = script_name

    def __call__(self, environ, start_response):
        environ['SCRIPT_NAME'] = self.script_name
        path_info = environ.get('PATH_INFO', '')
        if path_info.startswith(self.script_name):
            environ['PATH_INFO'] = path_info[len(self.script_name):]
        return self.app(environ, start_response)

application = ScriptNameFix(get_wsgi_application(), '/tjai')
