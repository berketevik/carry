"""Verify device-flow startup in an isolated CLI configuration; never complete login."""
import json
import os
from pathlib import Path
import tempfile
import time

from carry.github import Login

with tempfile.TemporaryDirectory(prefix='carry-login-check-') as temp:
    os.environ['GH_CONFIG_DIR']=temp
    os.environ['GH_TOKEN']=''
    os.environ['GITHUB_TOKEN']=''
    login=Login()
    deadline=time.monotonic()+30
    while login.result.get('state')=='starting' and time.monotonic()<deadline:
        time.sleep(.1)
    result=dict(device_code_received=bool(login.result.get('user_code')),
                verification_url_valid=login.result.get('verification_uri')=='https://github.com/login/device',
                login_completed=False,existing_credentials_unchanged=True)
    login.cancel(); login.process.wait(timeout=5)
    assert result['device_code_received'] and result['verification_url_valid'], 'device_flow_did_not_start'
    print(json.dumps(result))
