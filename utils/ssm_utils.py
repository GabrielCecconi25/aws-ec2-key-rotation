import time

def is_ssm_online(ssm, instance_id):
    response = ssm.describe_instance_information()
    return any(i["InstanceId"] == instance_id for i in response["InstanceInformationList"])

def execute_command(ssm, instance_id, command):
    response = ssm.send_command(
        InstanceIds=[instance_id],
        DocumentName="AWS-RunShellScript",
        Parameters={"commands": [command]},
        TimeoutSeconds=30,
    )
    return response["Command"]["CommandId"]

def get_ssm_response(ssm, command_id, instance_id):
    time.sleep(2)
    output = ssm.get_command_invocation(CommandId=command_id, InstanceId=instance_id)
    while output["Status"] in ["InProgress", "Pending"]:
        time.sleep(2)
        output = ssm.get_command_invocation(CommandId=command_id, InstanceId=instance_id)
    return output.get("StandardOutputContent", "")
