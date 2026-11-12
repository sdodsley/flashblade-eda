# Per-code runbook tasks

`playbooks/alert_runbook.yml` includes `runbooks/<alert code>.yml` relative
to itself (`{{ playbook_dir }}/runbooks/`; ansible-rulebook copies the whole
`playbooks/` directory into its temporary project, so the relative path
resolves) when the code's entry in `alert_runbooks` has `route: tasks`. Each
file is a task list that runs on localhost with the alert fields in scope:
`target_array`, `alert_id`, `alert_code`, `component`, `component_name`,
`alert_description`, `alert_variables`, `array_action`, `knowledge_base_url`,
`subject`, `severity`, `fb_api_version`. The array's credentials are resolved
onto `hostvars[target_array]` first: read the array with
`tasks/fb_rest.yml` (pass `fb_rest_url` and `fb_rest_token` from there) or
delegate a collection module to `{{ target_array }}`.

| File | Alert | What it does |
| --- | --- | --- |
| `9999.yml` | test fixture | prints that it ran; used by the replay test |
| `1101.yml` | file system space quota | reads the file system's provisioned size, usage and hard limit into the record |
| `1016.yml` | blade evacuation stuck, no space | reads the evacuating blade and the array's space so the ticket says how much is needed |
| `1139.yml` | syslog message drop (audit) | lists the syslog servers carrying the data-audit service and runs the array's connectivity test |
