def get_linux_instances_without_asg(ec2):
    instances = []
    paginator = ec2.get_paginator('describe_instances')
    for page in paginator.paginate(
        Filters=[
            {"Name": "instance-state-name", "Values": ["running", "stopped"]}
        ]
    ):
        for reservation in page["Reservations"]:
            for instance in reservation["Instances"]:
                # Excluir instâncias com Auto Scaling Group
                if any(tag["Key"] == "aws:autoscaling:groupName" for tag in instance.get("Tags", [])):
                    continue

                # Excluir Windows — se 'Platform' == 'windows' → é Windows
                if instance.get("Platform", "").lower() == "windows":
                    continue

                instances.append(instance)
    return instances
