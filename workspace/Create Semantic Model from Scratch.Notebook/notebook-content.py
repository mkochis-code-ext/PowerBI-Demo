# PowerBI notebook source

# METADATA ********************

# META {
# META   "kernel_info": {
# META     "name": "synapse_pyspark"
# META   },
# META   "dependencies": {
# META     "lakehouse": {
# META       "default_lakehouse": "01e76c04-58b1-4a27-ab52-a027df91bf84",
# META       "default_lakehouse_name": "DemoLakehouse",
# META       "default_lakehouse_workspace_id": "eae17da2-f404-4f12-88bc-1956b33b586c",
# META       "known_lakehouses": [
# META         {
# META           "id": "01e76c04-58b1-4a27-ab52-a027df91bf84"
# META         }
# META       ]
# META     },
# META     "environment": {
# META       "environmentId": "face3c08-5d1d-4ec8-8029-57cdf1a1f7af",
# META       "workspaceId": "f4eda307-e648-425e-9c89-6ef1ec852eeb"
# META     }
# META   }
# META }

# MARKDOWN ********************

# # Import the required packages and models

# CELL ********************

import sempy.fabric as fabric
import sempy_labs as labs
import sempy_labs.lakehouse as lakehouse
import pandas as pd
from sempy_labs.tom import connect_semantic_model
from datetime import datetime

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# MARKDOWN ********************

# # Define the parameters for the Direct Lake semantic model

# CELL ********************

semantic_model_name = "demo_semantic_model"
workspace = fabric.get_workspace_id()
tables = lakehouse.get_lakehouse_tables()['Table Name'].tolist()

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

lakehouse.get_lakehouse_tables()

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# MARKDOWN ********************

# # Optimize Lakehouse Tables

# CELL ********************

lakehouse.optimize_lakehouse_tables()

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# MARKDOWN ********************

# # Build the semantic model using the information define in the above cell

# CELL ********************

labs.directlake.generate_direct_lake_semantic_model(
     dataset = semantic_model_name 
    ,lakehouse_tables = tables
    ,overwrite = True
)

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# MARKDOWN ********************

# # Creates the model's table relationships

# MARKDOWN ********************

# Reads in the contents of the ***model_relationships.csv*** file into the ***pdf_relationship_data*** data frame

# CELL ********************

path = "/lakehouse/default/Files/model_information/model_relationships.csv"
pdf_relationship_data = pd.read_csv(path)
display(pdf_relationship_data)

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# MARKDOWN ********************

# Iterates over the ***pdf_relationship_data*** data frame and uses the information in the row to create the relationship using the information in the row

# CELL ********************

with connect_semantic_model(dataset=semantic_model_name, readonly=False) as tom:
    for index, row in pdf_relationship_data.iterrows():
        tom.add_relationship(
             from_table = row['from_table']
            ,from_column = row['from_column']
            ,from_cardinality = row['from_cardinality']
            ,to_table = row['to_table']
            ,to_column = row['to_column']
            ,to_cardinality = row['to_cardinality']
            ,is_active = True if row['is_active'] == 1 else False        
        )

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# MARKDOWN ********************

# # Configure semantic model's columns

# MARKDOWN ********************

# The ***fabric.list_columns()*** function returns information about each column in the specified semantic model. We will use some of the information provided to determine how we will configure the column.

# CELL ********************

fabric.list_columns(dataset = semantic_model_name)

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# MARKDOWN ********************

# In the following cell, we are subsetting the data frame returned by ***fabric.list_columns()*** to only include the columns specified in the ***columns*** list variable. 

# CELL ********************

columns = ["Table Name", "Column Name", "Hidden", "Is Available in MDX"]
fdf_ColumnInfo = fabric.list_columns(dataset = semantic_model_name, workspace=workspace)[columns]
display(fdf_ColumnInfo)


# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

fabric.refresh_tom_cache()

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

fact_tables = ['sales']

with connect_semantic_model(dataset=semantic_model_name, workspace=workspace, readonly=False) as tom:
    for index, row in fdf_ColumnInfo.iterrows():
        if (row['Table Name'] in fact_tables) | ((row['Table Name'] not in fact_tables) & (row['Column Name'].endswith('_key'))):
            tom.update_column(
                 table_name=row['Table Name']
                ,column_name=row['Column Name']
                ,is_available_in_mdx=False
                ,hidden=True
            )

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

fabric.refresh_tom_cache()

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

fdf_ColumnInfo = fabric.list_columns(dataset = semantic_model_name, workspace=workspace)[columns]
display(fdf_ColumnInfo)

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# MARKDOWN ********************

# # Add Measures

# CELL ********************

fdfSalesColumnInfo = fabric.list_columns(dataset=semantic_model_name, table="sales")
fdfSalesColumnInfo["Column Name"].str.startswith("Fake")

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

fdfSalesColumnInfo = fabric.list_columns(dataset=semantic_model_name, table="sales")
fdfSalesColumnInfo = fdfSalesColumnInfo[fdfSalesColumnInfo["Column Name"].str.startswith("Fake")]
display(fdfSalesColumnInfo)

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

with connect_semantic_model(dataset=semantic_model_name, workspace=workspace, readonly=False) as tom:
    for index, row in fdfSalesColumnInfo.iterrows():

        # Define variables
        table_name = row["Table Name"]
        measure_name = f'Total {row["Column Name"].replace("_"," ")}'
        expression = f'=SUM(sales[{row["Column Name"]}])'
        format_string = "#,##0"

        # Configure add_measure()
        tom.add_measure(
             table_name = table_name
            ,measure_name = measure_name
            ,expression = expression
            ,format_string = format_string
        )


# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

fabric.refresh_tom_cache()

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
