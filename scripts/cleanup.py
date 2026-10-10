"""Remove only resources belonging to this project's deployment."""
import argparse
import time
import boto3
from settings import deployment

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--confirm-stack', required=True, help='Exact stack name; deletes project resources and data')
args = parser.parse_args()
config = deployment()
if args.confirm_stack != config['StackName']:
    parser.error('Stack name does not match deployment.json.')
boto3.setup_default_session(region_name=config['Region'])
s3 = boto3.resource('s3')
s3.Bucket(config['WebsiteBucket']).objects.all().delete()
cf = boto3.client('cloudformation')
cf.delete_stack(StackName=config['StackName'])
print('Stack deletion requested. Waiting for OpenSearch and all project resources to be removed.', flush=True)
while True:
    try:
        status = cf.describe_stacks(StackName=config['StackName'])['Stacks'][0]['StackStatus']
    except cf.exceptions.ClientError as exc:
        if 'does not exist' in str(exc): break
        raise
    if status == 'DELETE_FAILED':
        raise SystemExit('Deletion failed. Inspect CloudFormation events and remove the remaining resources.')
    time.sleep(15)
s3.Bucket(config['ArtifactBucket']).objects.all().delete()
s3.Bucket(config['ArtifactBucket']).delete()
print('Project stack and artifact bucket deleted. SES identity retained for your other applications.')
