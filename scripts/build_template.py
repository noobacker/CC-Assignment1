"""Generate a reviewable CloudFormation template from the original Swagger."""
import json
from pathlib import Path
import sys
import yaml
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from lf1 import PROMPTS
from common import CUISINES

def ref(name): return {'Ref': name}
def sub(value): return {'Fn::Sub': value}
def arn(name): return {'Fn::GetAtt': [name, 'Arn']}
def search_if(yes, no): return {'Fn::If': ['SearchEnabled', yes, no]}
def resource(kind, props, **extra): return {'Type': 'AWS::' + kind, 'Properties': props, **extra}
def statement(actions, resources): return {'Effect': 'Allow', 'Action': actions, 'Resource': resources}
def role(service, statements):
    props = {'AssumeRolePolicyDocument': {'Version': '2012-10-17', 'Statement': [{
        'Effect': 'Allow', 'Principal': {'Service': service}, 'Action': 'sts:AssumeRole'}]}}
    if statements:
        props['Policies'] = [{'PolicyName': 'ApplicationAccess', 'PolicyDocument': {
            'Version': '2012-10-17', 'Statement': statements}}]
    return resource('IAM::Role', props)

def build():
    r = {}
    r['Website'] = resource('S3::Bucket', {
        'WebsiteConfiguration': {'IndexDocument': 'index.html', 'ErrorDocument': 'index.html'},
        'OwnershipControls': {'Rules': [{'ObjectOwnership': 'BucketOwnerEnforced'}]},
        'PublicAccessBlockConfiguration': {'BlockPublicAcls': True, 'IgnorePublicAcls': True,
                                         'BlockPublicPolicy': False, 'RestrictPublicBuckets': False}})
    r['WebsitePolicy'] = resource('S3::BucketPolicy', {'Bucket': ref('Website'), 'PolicyDocument': {
        'Version': '2012-10-17', 'Statement': [{'Effect': 'Allow', 'Principal': '*',
            'Action': 's3:GetObject', 'Resource': sub('${Website.Arn}/*')}]}})
    r['Restaurants'] = resource('DynamoDB::Table', {'TableName': 'yelp-restaurants',
        'BillingMode': 'PAY_PER_REQUEST', 'AttributeDefinitions': [{'AttributeName': 'BusinessID', 'AttributeType': 'S'}],
        'KeySchema': [{'AttributeName': 'BusinessID', 'KeyType': 'HASH'}]})
    r['State'] = resource('DynamoDB::Table', {'BillingMode': 'PAY_PER_REQUEST',
        'AttributeDefinitions': [{'AttributeName': 'pk', 'AttributeType': 'S'}],
        'KeySchema': [{'AttributeName': 'pk', 'KeyType': 'HASH'}],
        'TimeToLiveSpecification': {'AttributeName': 'expiresAt', 'Enabled': True}})
    r['DeadLetterQueue'] = resource('SQS::Queue', {'FifoQueue': True, 'MessageRetentionPeriod': 1209600,
                                                'SqsManagedSseEnabled': True})
    r['Q1'] = resource('SQS::Queue', {'FifoQueue': True, 'VisibilityTimeout': 180,
        'SqsManagedSseEnabled': True, 'MessageRetentionPeriod': 345600,
        'RedrivePolicy': {'deadLetterTargetArn': arn('DeadLetterQueue'), 'maxReceiveCount': 5}})
    logs = [statement(['logs:CreateLogStream', 'logs:PutLogEvents'],
                     sub('arn:${AWS::Partition}:logs:${AWS::Region}:${AWS::AccountId}:log-group:/aws/lambda/${AWS::StackName}-*:*'))]
    state_access = statement(['dynamodb:GetItem', 'dynamodb:PutItem', 'dynamodb:UpdateItem', 'dynamodb:DeleteItem'], arn('State'))
    search_arn = sub('arn:${AWS::Partition}:es:${AWS::Region}:${AWS::AccountId}:domain/${SearchDomainName}/*')
    r['LF0Role'] = role('lambda.amazonaws.com', logs + [statement('lex:RecognizeText',
        sub('arn:${AWS::Partition}:lex:${AWS::Region}:${AWS::AccountId}:bot-alias/*'))])
    r['LF1Role'] = role('lambda.amazonaws.com', logs + [statement('sqs:SendMessage', arn('Q1')),
                                                       statement('dynamodb:GetItem', arn('State'))])
    r['LF2Role'] = role('lambda.amazonaws.com', logs + [state_access,
        statement(['sqs:ReceiveMessage', 'sqs:DeleteMessage', 'sqs:GetQueueAttributes'], arn('Q1')),
        statement('dynamodb:GetItem', arn('Restaurants')), statement('es:ESHttpPost', search_arn),
        statement('ses:SendEmail', sub('arn:${AWS::Partition}:ses:${AWS::Region}:${AWS::AccountId}:identity/${SenderEmail}'))])
    r['LexRole'] = role('lexv2.amazonaws.com', [])
    r['Search'] = resource('OpenSearchService::Domain', {
        'DomainName': ref('SearchDomainName'), 'EngineVersion': ref('SearchEngineVersion'),
        'ClusterConfig': {'InstanceType': 't3.small.search', 'InstanceCount': 1, 'ZoneAwarenessEnabled': False},
        'EBSOptions': {'EBSEnabled': True, 'VolumeType': 'gp3', 'VolumeSize': 10},
        'EncryptionAtRestOptions': {'Enabled': True}, 'NodeToNodeEncryptionOptions': {'Enabled': True},
        'DomainEndpointOptions': {'EnforceHTTPS': True, 'TLSSecurityPolicy': 'Policy-Min-TLS-1-2-2019-07'},
        'AccessPolicies': {'Version': '2012-10-17', 'Statement': [{
            'Effect': 'Allow', 'Principal': {'AWS': [arn('LF2Role'), ref('DeployerArn')]},
            'Action': 'es:ESHttp*', 'Resource': search_arn}]}}, Condition='SearchEnabled')
    env = {'LF0': {'LEX_BOT_ID': ref('Bot'), 'LEX_ALIAS_ID': {'Fn::GetAtt': ['BotAlias', 'BotAliasId']},
                   'ALLOWED_ORIGIN': {'Fn::GetAtt': ['Website', 'WebsiteURL']}, 'BOILERPLATE_MODE': 'false'},
           'LF1': {'QUEUE_URL': ref('Q1'), 'STATE_TABLE': ref('State')},
           'LF2': {'QUEUE_URL': ref('Q1'), 'STATE_TABLE': ref('State'), 'WORKER_LOCK_ENABLED': 'true', 'RESTAURANTS_TABLE': ref('Restaurants'),
                   'OPENSEARCH_ENDPOINT': search_if({'Fn::GetAtt': ['Search', 'DomainEndpoint']}, ''), 'SES_SENDER_EMAIL': ref('SenderEmail')}}
    for name in env:
        r[name + 'Log'] = resource('Logs::LogGroup', {'LogGroupName': sub('/aws/lambda/${AWS::StackName}-' + name), 'RetentionInDays': 14})
        props = {'FunctionName': sub('${AWS::StackName}-' + name), 'Runtime': 'python3.12',
                 'Handler': name.lower() + '.handler', 'Role': arn(name + 'Role'),
                 'Timeout': 120 if name == 'LF2' else 25, 'MemorySize': 256,
                 'Code': {'S3Bucket': ref('ArtifactBucket'), 'S3Key': ref('CodeKey')},
                 'Environment': {'Variables': env[name]}}
        r[name] = resource('Lambda::Function', props, DependsOn=[name + 'Log'])
    custom_types = []
    for name, values in [('CuisineType', CUISINES), ('ReuseType', ('yes', 'no'))]:
        custom_types.append({'Name': name, 'SlotTypeValues': [{'SampleValue': {'Value': v}} for v in values],
                            'ValueSelectionSetting': {'ResolutionStrategy': 'TOP_RESOLUTION'}})
    slot_types = {'Location': 'AMAZON.City', 'Cuisine': 'CuisineType', 'NumberOfPeople': 'AMAZON.Number',
                  'DiningDate': 'AMAZON.Date', 'DiningTime': 'AMAZON.Time', 'Email': 'AMAZON.EmailAddress'}
    slots = [{'Name': n, **({'SlotTypeName': t}), 'ValueElicitationSetting': {
        'SlotConstraint': 'Required', 'PromptSpecification': {'MaxRetries': 3,
            'MessageGroupsList': [{'Message': {'PlainTextMessage': {'Value': PROMPTS[n]}}}]}}}
        for n, t in slot_types.items()]
    slots.append({'Name': 'ReusePrevious', 'SlotTypeName': 'ReuseType', 'ValueElicitationSetting': {
        'SlotConstraint': 'Optional', 'PromptSpecification': {'MaxRetries': 3, 'MessageGroupsList': [
            {'Message': {'PlainTextMessage': {'Value': 'Would you like the same recommendations? Say yes or no.'}}}]}}})
    intents = []
    for name, utterances in [
        ('GreetingIntent', ['hello', 'hi', 'hey', 'good morning']),
        ('ThankYouIntent', ['thanks', 'thank you', 'thank you very much']),
        ('DiningSuggestionsIntent', ['I need restaurant suggestions', 'recommend a restaurant',
             'I want to eat', 'find me a restaurant', 'I am hungry', 'I want {Cuisine} food',
             'I want {Cuisine} food in {Location}', 'restaurant suggestions'])]:
        intent = {'Name': name, 'SampleUtterances': [{'Utterance': u} for u in utterances],
                  'DialogCodeHook': {'Enabled': True}, 'FulfillmentCodeHook': {'Enabled': True, 'IsActive': True}}
        if name == 'DiningSuggestionsIntent':
            intent['Slots'] = slots
            intent['SlotPriorities'] = [{'Priority': priority, 'SlotName': slot['Name']}
                                       for priority, slot in enumerate(slots, 1)]
        intents.append(intent)
    intents.append({'Name': 'FallbackIntent', 'ParentIntentSignature': 'AMAZON.FallbackIntent',
                    'FulfillmentCodeHook': {'Enabled': True, 'IsActive': True}})
    r['Bot'] = resource('Lex::Bot', {'Name': sub('${AWS::StackName}-DiningConcierge'),
        'RoleArn': arn('LexRole'), 'DataPrivacy': {'ChildDirected': False}, 'IdleSessionTTLInSeconds': 900,
        'AutoBuildBotLocales': True, 'BotLocales': [{'LocaleId': 'en_US', 'NluConfidenceThreshold': 0.4,
                                                   'SlotTypes': custom_types, 'Intents': intents}]})
    r['BotVersion'] = resource('Lex::BotVersion', {'BotId': ref('Bot'),
        'BotVersionLocaleSpecification': [{'LocaleId': 'en_US', 'BotVersionLocaleDetails': {'SourceBotVersion': 'DRAFT'}}]})
    r['BotAlias'] = resource('Lex::BotAlias', {'BotId': ref('Bot'), 'BotAliasName': 'production',
        'BotVersion': {'Fn::GetAtt': ['BotVersion', 'BotVersion']},
        'BotAliasLocaleSettings': [{'LocaleId': 'en_US', 'BotAliasLocaleSetting': {'Enabled': True,
            'CodeHookSpecification': {'LambdaCodeHook': {'CodeHookInterfaceVersion': '1.0', 'LambdaArn': arn('LF1')}}}}]}, DependsOn=['LexInvoke'])
    r['LexInvoke'] = resource('Lambda::Permission', {'FunctionName': ref('LF1'), 'Action': 'lambda:InvokeFunction',
        'Principal': 'lexv2.amazonaws.com', 'SourceAccount': ref('AWS::AccountId'),
        'SourceArn': sub('arn:${AWS::Partition}:lex:${AWS::Region}:${AWS::AccountId}:bot-alias/${Bot}/*')})
    r['MinuteSchedule'] = resource('Events::Rule', {'ScheduleExpression': 'rate(1 minute)',
        'State': search_if('ENABLED', 'DISABLED'), 'Targets': [{'Arn': arn('LF2'), 'Id': 'LF2'}]})
    r['ScheduleInvoke'] = resource('Lambda::Permission', {'FunctionName': ref('LF2'),
        'Action': 'lambda:InvokeFunction', 'Principal': 'events.amazonaws.com', 'SourceArn': arn('MinuteSchedule')})
    swagger = yaml.safe_load((ROOT / 'swagger/swagger.yaml').read_text())
    swagger['paths']['/chatbot']['post']['x-amazon-apigateway-integration'] = {
        'type': 'aws_proxy', 'httpMethod': 'POST',
        'uri': sub('arn:${AWS::Partition}:apigateway:${AWS::Region}:lambda:path/2015-03-31/functions/${LF0.Arn}/invocations')}
    # Preflight responses must include CORS independently of Lambda.
    cors = {'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Methods': 'POST,OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type,X-Session-Id'}
    swagger['paths']['/chatbot']['options'] = {'responses': {'200': {'description': 'CORS',
        'headers': {key: {'type': 'string'} for key in cors}}}, 'x-amazon-apigateway-integration': {
        'type': 'mock', 'requestTemplates': {'application/json': '{"statusCode":200}'}, 'responses': {'default': {
            'statusCode': '200', 'responseParameters': {'method.response.header.' + k: "'" + v + "'" for k, v in cors.items()}}}}}
    swagger['x-amazon-apigateway-gateway-responses'] = {kind: {'responseParameters': {
        'gatewayresponse.header.Access-Control-Allow-Origin': "'*'"}} for kind in ['DEFAULT_4XX', 'DEFAULT_5XX']}
    r['Api'] = resource('ApiGateway::RestApi', {'Name': sub('${AWS::StackName}-API'), 'Body': swagger,
                                             'EndpointConfiguration': {'Types': ['REGIONAL']}})
    r['ApiDeployment'] = resource('ApiGateway::Deployment', {'RestApiId': ref('Api')})
    r['ApiStage'] = resource('ApiGateway::Stage', {'RestApiId': ref('Api'), 'DeploymentId': ref('ApiDeployment'),
        'StageName': 'v1', 'MethodSettings': [{'ResourcePath': '/*', 'HttpMethod': '*',
            'ThrottlingBurstLimit': 10, 'ThrottlingRateLimit': 5}]})
    r['ApiInvoke'] = resource('Lambda::Permission', {'FunctionName': ref('LF0'), 'Action': 'lambda:InvokeFunction',
        'Principal': 'apigateway.amazonaws.com', 'SourceArn': sub('arn:${AWS::Partition}:execute-api:${AWS::Region}:${AWS::AccountId}:${Api}/*/POST/chatbot')})
    outputs = {'WebsiteBucket': ref('Website'), 'WebsiteUrl': {'Fn::GetAtt': ['Website', 'WebsiteURL']},
               'ApiUrl': sub('https://${Api}.execute-api.${AWS::Region}.${AWS::URLSuffix}/v1'),
               'ApiId': ref('Api'), 'BotId': ref('Bot'), 'BotAliasId': {'Fn::GetAtt': ['BotAlias', 'BotAliasId']},
               'QueueUrl': ref('Q1'), 'DeadLetterQueueUrl': ref('DeadLetterQueue'), 'StateTable': ref('State'),
               'RestaurantsTable': ref('Restaurants'), 'SearchEndpoint': search_if({'Fn::GetAtt': ['Search', 'DomainEndpoint']}, ''),
               'SearchEngineVersion': ref('SearchEngineVersion'), 'LF2Name': ref('LF2')}
    return {'AWSTemplateFormatVersion': '2010-09-09', 'Description': 'Dining Concierge - CC Fall 2026 Assignment 1',
            'Conditions': {'SearchEnabled': {'Fn::Equals': [ref('ProvisionSearch'), 'true']}},
            'Parameters': {'ProvisionSearch': {'Type': 'String', 'Default': 'true', 'AllowedValues': ['true', 'false']},
                'ArtifactBucket': {'Type': 'String'}, 'CodeKey': {'Type': 'String'},
                'SenderEmail': {'Type': 'String'}, 'DeployerArn': {'Type': 'String'},
                'SearchDomainName': {'Type': 'String', 'Default': 'dining-concierge', 'MaxLength': 28},
                'SearchEngineVersion': {'Type': 'String', 'Default': 'Elasticsearch_7.10',
                                       'AllowedValues': ['Elasticsearch_7.10', 'OpenSearch_2.19']}},
            'Resources': r, 'Outputs': {k: {'Value': v} for k, v in outputs.items()}}

if __name__ == '__main__':
    target = ROOT / 'infrastructure/template.json'
    target.write_text(json.dumps(build(), indent=2) + '\n')
    print(target)
