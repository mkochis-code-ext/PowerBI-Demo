# Fabric notebook source

# METADATA ********************

# META {
# META   "kernel_info": {
# META     "name": "synapse_pyspark"
# META   },
# META   "dependencies": {
# META     "environment": {
# META       "environmentId": "face3c08-5d1d-4ec8-8029-57cdf1a1f7af",
# META       "workspaceId": "f4eda307-e648-425e-9c89-6ef1ec852eeb"
# META     }
# META   }
# META }

# CELL ********************

import sempy_labs as labs
import sempy.fabric as fabric

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

workspace = fabric.get_workspace_id()
datasets = fabric.list_datasets()['Dataset Name']
for dataset in datasets:
    try:
        labs.delete_semantic_model(dataset=dataset, workspace=workspace)
        print(f"Successfully deleted the {dataset} data set.")
    except Exception as e:
        print(f"Was not able to delete the {dataset} data set due to the following reasons: {e}")

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************


# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }
