"""Deploy the complete assignment with CloudFormation, then publish the S3 frontend."""
import argparse
import hashlib
import json
import mimetypes
import os
import re
import sys
import time
import zipfile
import boto3
from botocore.exceptions import ClientError
from settings import ROOT, load_env
from build_template import build

def versioned_template(template):
    """Rename immutable resources and their references, never property names."""
    template = json.loads(json.dumps(template))
    for logical in ['BotVersion', 'ApiDeployment']:
        source = template['Resources']['Bot' if logical == 'BotVersion' else 'Api']
        suffix = hashlib.sha256(json.dumps(source, sort_keys=True).encode()).hexdigest()[:10]
        new_name = logical + suffix
        template['Resources'][new_name] = template['Resources'].pop(logical)
        def visit(node):
            if isinstance(node, dict):
                if node.get('Ref') == logical:
                    node['Ref'] = new_name
                attribute = node.get('Fn::GetAtt')
                if isinstance(attribute, list) and attribute[0] == logical:
                    attribute[0] = new_name
                elif isinstance(attribute, str) and attribute.startswith(logical + '.'):
                    node['Fn::GetAtt'] = new_name + attribute[len(logical):]
                depends = node.get('DependsOn')
                if depends == logical:
                    node['DependsOn'] = new_name
                elif isinstance(depends, list):
                    node['DependsOn'] = [new_name if v == logical else v for v in depends]
                for value in node.values(): visit(value)
            elif isinstance(node, list):
                for value in node: visit(value)
        visit(template)
    return template

def wait_stack(cf, name):
    previous = None
    deadline = time.monotonic() + 7200
    while time.monotonic() < deadline:
        stack = cf.describe_stacks(StackName=name)['Stacks'][0]
        status = stack['StackStatus']
        if status != previous:
            print('Stack:', status, flush=True)
            previous = status
        if status in {'CREATE_COMPLETE', 'UPDATE_COMPLETE'}:
            return stack
        if not status.endswith('_IN_PROGRESS'):
            events = cf.describe_stack_events(StackName=name)['StackEvents']
            for event in events:
                if 'FAILED' in event['ResourceStatus']:
                    print(event['LogicalResourceId'], event.get('ResourceStatusReason', ''))
            raise RuntimeError(f'Stack ended in {status}. Resolve the reported AWS error before retrying.')
        time.sleep(15)
    raise TimeoutError('Stack still provisioning. Check CloudFormation and rerun after completion.')

def main():
    load_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stack', default='dining-concierge')
    parser.add_argument('--region', default=os.environ.get('AWS_DEFAULT_REGION', 'us-east-1'))
    parser.add_argument('--sender', default=os.environ.get('SES_SENDER_EMAIL'))
    parser.add_argument('--engine', default='Elasticsearch_7.10', choices=['Elasticsearch_7.10', 'OpenSearch_2.19'])
    parser.add_argument('--verify-email', action='store_true', help='Send SES verification email to the configured sender')
    parser.add_argument('--without-search', action='store_true', help='Deploy the app first; defer the search instance and queue schedule until data is ready')
    args = parser.parse_args()
    if not args.sender or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', args.sender):
        parser.error('Set SES_SENDER_EMAIL in .env or pass --sender with your email.')
    if not re.fullmatch(r'[a-z][a-z0-9-]{2,27}', args.stack):
        parser.error('--stack must be 3-28 lowercase letters, digits or hyphens and start with a letter.')
    session = boto3.Session(region_name=args.region)
    identity = session.client('sts').get_caller_identity()  # Fail before any mutation on invalid credentials.
    account = identity['Account']
    principal = identity['Arn']
    if ':assumed-role/' in principal:
        principal = session.client('iam').get_role(RoleName=principal.split('/')[-2])['Role']['Arn']
    print(f'Deploying to account {account}, region {args.region}.', flush=True)
    print('Search instance deferred.' if args.without_search else 'OpenSearch is a continuously billed instance. Run cleanup after grading. Engine: ' + args.engine, flush=True)
    ses = session.client('ses')
    verification = ses.get_identity_verification_attributes(Identities=[args.sender])['VerificationAttributes']
    if verification.get(args.sender, {}).get('VerificationStatus') != 'Success':
        if args.verify_email:
            ses.verify_email_identity(EmailAddress=args.sender)
            print('SES verification email requested. Click its link before testing email delivery.')
        else:
            print('SES sender is not verified. Rerun with --verify-email and click the verification link.')
    s3 = session.client('s3')
    bucket = f'{args.stack}-artifacts-{account}-{args.region}'
    try:
        s3.head_bucket(Bucket=bucket)
    except ClientError as exc:
        if exc.response['Error']['Code'] not in {'404', 'NoSuchBucket', 'NotFound'}:
            raise
        props = {'Bucket': bucket}
        if args.region != 'us-east-1': props['CreateBucketConfiguration'] = {'LocationConstraint': args.region}
        s3.create_bucket(**props)
        s3.put_public_access_block(Bucket=bucket, PublicAccessBlockConfiguration={
            'BlockPublicAcls': True, 'IgnorePublicAcls': True, 'BlockPublicPolicy': True, 'RestrictPublicBuckets': True})
    build_dir = ROOT / '.build'
    build_dir.mkdir(exist_ok=True)
    archive = build_dir / 'functions.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as output:
        for path in sorted((ROOT / 'src').glob('*.py')):
            output.write(path, path.name)
    key = 'lambda/' + hashlib.sha256(archive.read_bytes()).hexdigest() + '.zip'
    s3.upload_file(str(archive), bucket, key)
    template = versioned_template(build())
    text = json.dumps(template, indent=2)
    (build_dir / 'deployed-template.json').write_text(text + '\n')
    cf = session.client('cloudformation')
    cf.validate_template(TemplateBody=text)
    parameters = {'ArtifactBucket': bucket, 'CodeKey': key, 'SenderEmail': args.sender,
                  'DeployerArn': principal, 'SearchDomainName': args.stack, 'SearchEngineVersion': args.engine,
                  'ProvisionSearch': 'false' if args.without_search else 'true'}
    options = {'StackName': args.stack, 'TemplateBody': text, 'Capabilities': ['CAPABILITY_IAM'],
               'Parameters': [{'ParameterKey': k, 'ParameterValue': v} for k, v in parameters.items()]}
    try:
        existing = cf.describe_stacks(StackName=args.stack)['Stacks'][0]
        exists = True
        if existing['StackStatus'] == 'ROLLBACK_COMPLETE':
            resources = cf.list_stack_resources(StackName=args.stack)['StackResourceSummaries']
            live = [r for r in resources if r['ResourceStatus'] != 'DELETE_COMPLETE']
            if live:
                raise RuntimeError('Failed stack still contains resources. Inspect it before retrying.')
            print('Removing the empty failed stack before retrying.', flush=True)
            cf.delete_stack(StackName=args.stack)
            cf.get_waiter('stack_delete_complete').wait(StackName=args.stack, WaiterConfig={'Delay': 5, 'MaxAttempts': 60})
            exists = False
    except ClientError as exc:
        if 'does not exist' not in str(exc): raise
        exists = False
    if exists:
        try:
            cf.update_stack(**options)
        except ClientError as exc:
            if 'No updates are to be performed' not in str(exc): raise
    else:
        cf.create_stack(**options, Tags=[{'Key': 'Project', 'Value': 'CC-Fall2026-Assignment1'}])
    stack = wait_stack(cf, args.stack)
    outputs = {o['OutputKey']: o['OutputValue'] for o in stack['Outputs']}
    lex = session.client('lexv2-models')
    alias = lex.describe_bot_alias(botId=outputs['BotId'], botAliasId=outputs['BotAliasId'])
    locale = lex.describe_bot_locale(botId=outputs['BotId'], botVersion=alias['botVersion'], localeId='en_US')
    if locale['botLocaleStatus'] != 'Built':
        raise RuntimeError('Lex locale is not built: ' + str(locale.get('failureReasons', locale['botLocaleStatus'])))
    outputs.update({'Region': args.region, 'StackName': args.stack, 'ArtifactBucket': bucket, 'SenderEmail': args.sender})
    (ROOT / 'deployment.json').write_text(json.dumps(outputs, indent=2) + '\n')
    (ROOT / 'frontend/config.js').write_text('window.CONCIERGE_CONFIG = ' + json.dumps({'apiBaseUrl': outputs['ApiUrl']}) + ';\n')
    for path in (ROOT / 'frontend').rglob('*'):
        if path.is_file() and not path.name.endswith('.example.js') and path.name != '.DS_Store':
            mime = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
            s3.upload_file(str(path), outputs['WebsiteBucket'], path.relative_to(ROOT / 'frontend').as_posix(),
                           ExtraArgs={'ContentType': mime, 'CacheControl': 'no-cache'})
    print('Website:', outputs['WebsiteUrl'])
    print('Next: python scripts/ingest_yelp.py, then python scripts/verify_live.py')

if __name__ == '__main__':
    main()
