"""Zero-inference setup/inspection in the task's installed plugin home."""
import importlib.util,json,sys
from pathlib import Path
import os
home=Path(os.environ['HERMES_HOME']);root=home/'plugins/botsesh'
spec=importlib.util.spec_from_file_location('useful_chat_plugin',root/'__init__.py',submodule_search_locations=[str(root)])
pkg=importlib.util.module_from_spec(spec);sys.modules[spec.name]=pkg;spec.loader.exec_module(pkg)
from useful_chat_plugin.client import Client
client=Client()
command=json.load(sys.stdin)
if command['op']=='join':
    joined=client.join(command['invitation'],dev_origin=command['origin'])
    print(json.dumps({k:joined[k] for k in ('room_id','participant_id')}))
elif command['op']=='state':
    binding=client.store.room(command['room_id'])
    print(json.dumps({'participant_id':binding['participant_id'],'receipts':binding.get('processing_receipts',{}),'handled':binding.get('handled',[]),'health':binding.get('health',{})}))
elif command['op']=='config':
    from hermes_cli.config import load_config_readonly
    from hermes_cli.tools_config import _get_platform_tools
    print(json.dumps({'toolsets':sorted(_get_platform_tools(load_config_readonly(),'botsesh'))}))
elif command['op']=='detach':
    client.store.update(lambda d:d['rooms'][command['room_id']].update(active=False))
    print(json.dumps({'detached':True}))
else:raise ValueError('unknown local fixture op')
