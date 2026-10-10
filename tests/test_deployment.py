from build_template import build
from deploy import versioned_template

def test_version_resource_renaming_preserves_cloudformation_properties():
    original=build()
    rendered=versioned_template(original)
    alias=rendered['Resources']['BotAlias']['Properties']
    assert 'BotVersion' in alias
    version_id=alias['BotVersion']['Fn::GetAtt'][0]
    assert version_id.startswith('BotVersion')
    assert alias['BotVersion']['Fn::GetAtt'][1]=='BotVersion'
    assert version_id in rendered['Resources']
    deployment_id=rendered['Resources']['ApiStage']['Properties']['DeploymentId']['Ref']
    assert deployment_id.startswith('ApiDeployment') and deployment_id in rendered['Resources']
    assert 'BotVersion' in original['Resources']
    assert versioned_template(original)==rendered

def test_all_dining_slots_have_unique_priorities():
    locale = build()['Resources']['Bot']['Properties']['BotLocales'][0]
    intent = next(intent for intent in locale['Intents'] if intent['Name'] == 'DiningSuggestionsIntent')
    priorities = intent['SlotPriorities']
    assert {entry['SlotName'] for entry in priorities} == {slot['Name'] for slot in intent['Slots']}
    assert len({entry['Priority'] for entry in priorities}) == len(intent['Slots'])
