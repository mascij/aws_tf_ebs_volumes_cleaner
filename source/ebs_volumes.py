"""
Reports and deletes EBS volumes that are not in use
and have been idle for more than the specified time
"""
import csv
from datetime import datetime, timedelta, date
import logging
import os
import sys
import botocore
import boto3

REGION = os.getenv("AWS_DEFAULT_REGION")  # should this be changed to iterate through regions or manually input in sandbox for
HANDLER = logging.StreamHandler(sys.stdout)
HANDLER.setLevel(logging.INFO)
LOGGER = logging.getLogger("volume_sweeper")
LOGGER.addHandler(HANDLER)
LOGGER.setLevel(logging.INFO)
today = date.today()
date_time = today.strftime("%m/%d/%Y")
datelimit = datetime.today() - timedelta(days=7)
DR = False

def get_idle_time(volume_id, filter_date, region):
    """
    Get volume idle time on an individual volume over a specified
    windows
    """
    today = datetime.utcnow() + timedelta(days=1)
    cloudwatch = boto3.client("cloudwatch", region_name=region)
    metrics = cloudwatch.get_metric_statistics(
        Namespace="AWS/EBS",
        MetricName="VolumeIdleTime",
        Dimensions=[{"Name": "VolumeId", "Value": volume_id}],
        Period=3600,  # hourly metrics
        StartTime=filter_date,
        EndTime=today,
        Statistics=["Minimum"],
        Unit="Seconds",
    )
    return metrics["Datapoints"]


def is_idle(volume, filter_date, region):
    """Make sure the volume has not been used in the past two weeks"""
    metrics = get_idle_time(volume.id, filter_date, region)
    if metrics:
        for metric in metrics:
            # idle time is 5 minute interval aggregate
            if metric["Minimum"] < 299:
                return False
    # if the volume had no metrics lower than 299 it's probably not
    # actually being used for anything so we can include it as
    # a candidate for deletion
    return True


def get_available_volumes(filter_date, region):
    """
    Returns a list of Volume IDs for volumes that are not in use
    and have been idle for more than the specified time
    """
    ec2 = boto3.resource("ec2", region_name=region)
    volumes = ec2.volumes.filter(Filters=[{"Name": "status", "Values": ["available"]}])

    available_volumes = [
        vol for vol in volumes if vol.create_time.replace(tzinfo=None) < filter_date
    ]
    LOGGER.info(
        f"Found {len(available_volumes)} available volumes created before {filter_date}"
    )
    return available_volumes


def get_idle_volumes(days, filter_date, region):
    volumes = get_available_volumes(filter_date, region)
    idle_volumes = [vol for vol in volumes if is_idle(vol, filter_date, region)]
    LOGGER.info(
        f"{len(idle_volumes)} available volumes have been idle for {days} days or more"
    )
    return idle_volumes


def get_filter_date(number_of_days):
    today = datetime.utcnow() + timedelta(
        days=1
    )  # today + 1 because we want all of today
    return today - timedelta(days=number_of_days)


def get_region(region):
    if not region and not REGION:
        raise ValueError("Region must be provided")
    if region:
        return region
    return REGION


def write_file(volumes, filename, region):
    """
    Write results of search to a file in CSV format
    """
    with open(filename, "w") as csvfile:
        writer = csv.writer(csvfile, delimiter=",")
        writer.writerow(["region", "volume ID", "State", "Creation time", "Tags"])
        for volume in volumes:
            if volume.tags:
                tags = [
                    f"{tag['Key']}:{tag['Value']}" for tag in volume.tags if volume.tags
                ]
            else:
                tags = []
            writer.writerow(
                [
                    region,
                    volume.id,
                    volume.state,
                    volume.create_time.date().isoformat(),
                    " ".join(tags), "test"
                ]
            )

def delete_volumes(volumes):
    
    for volume in volumes:
        Protection = True
        ReviewDate = False

        if volume.tags != None:
            if any(t.get('Key') == 'Protection' for t in volume.tags):
                print(f"{volume.id} has been protected")
                continue
            if any(t.get('Key') == 'DateReviewed' for t in volume.tags):
                print(f"{volume.id} has DateReviewed")
                ReviewDate = True
                for t in volume.tags:      
                    if  t.get('Key') == 'DateReviewed' and t.get('Value') < datelimit.strftime("%m/%d/%Y"):
                        print(f"{volume.id} has DateReviewed < 7")
                        Protection = False

           
        if ReviewDate == False:
            volume.create_tags(
            DryRun=DR,
            Tags=[
                    {
                        'Key': 'DateReviewed',
                        'Value': date_time
                    },
                ]
            )
        
            print(f"{volume.id} has been tag")
            
            
        if Protection == False:
            snapshot_volumes(volume)
            volume.delete( DryRun=DR)
            print(f"Deleted {volume.id}")

def snapshot_volumes(volume):
    now = datetime.utcnow()
    client = boto3.client('ec2')
    #for volume in volumes:
    try:
        create_snapshot_response = client.create_snapshot(
            VolumeId=volume.id,
            TagSpecifications=[
                {
                    'ResourceType': 'snapshot',
                    'Tags': [
                        {
                            'Key': 'Name',
                            'Value': "%s-%s" %(volume.id,now)
                        },
                    ]
                },
            ],
            DryRun=DR
        )
        LOGGER.info(
        f" Took a snapshot of {volume.id}")
    except botocore.exceptions.ClientError as e:
        LOGGER.info(e)

def main():

    # regions = os.environ['REGIONS'].split(",")  # get_region(args.region)
    info = "Waste"
    ec2 = boto3.client('ec2')
    response = ec2.describe_regions().get('Regions')
    regions = [item.get('RegionName') for item in response]
    for region in regions:
        LOGGER.info(
            f"Finding EBS volumes that have been idle for {os.environ['DAYS']} days in region {region}"
        )
        filter_date = get_filter_date(int(os.environ['DAYS']))
        volumes = get_idle_volumes(os.environ['DAYS'], filter_date, region)
        delete_volumes(volumes)



def lambda_handler(event, context):
    main()

#lambda_handler(None, None)