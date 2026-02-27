# Fabric notebook source

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
# META     }
# META   }
# META }

# MARKDOWN ********************

# ## Saves the *Dates.csv* file as a spark table

# CELL ********************

sdfDates = spark.read.format("csv").option("header","true").load("Files/semantic_model_sourcedata/Dates.csv")
sdfDates.createOrReplaceTempView("vwDates")
display(sdfDates)

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

# MAGIC %%sql
# MAGIC DROP TABLE IF EXISTS dim_dates;
# MAGIC 
# MAGIC CREATE TABLE dim_dates
# MAGIC AS
# MAGIC 
# MAGIC SELECT
# MAGIC      to_date(c.date_key, 'M/d/yyyy') AS date_key
# MAGIC     ,CAST(c.`Year` AS INT) AS `Year`
# MAGIC     ,c.Year_Quarter AS Year_Quarter
# MAGIC     ,c.Year_Quarter_Number AS Year_Quarter_Number
# MAGIC     ,c.`Quarter` AS Quarter
# MAGIC     ,c.Year_Month AS Year_Month       
# MAGIC     ,c.Year_Month_Number AS Year_Month_Number
# MAGIC     ,c.`Month` AS Month
# MAGIC     ,c.Month_Number AS Month_number
# MAGIC     ,c.`Weekday` AS Weekday
# MAGIC     ,c.`Weekday_Number` AS Weekday_Number
# MAGIC FROM vwdates AS c

# METADATA ********************

# META {
# META   "language": "sparksql",
# META   "language_group": "synapse_pyspark"
# META }

# MARKDOWN ********************

# ## Saves the *Customer.csv* file as a spark table

# CELL ********************

sdfCustomers = spark.read.format("csv").option("header","true").load("Files/semantic_model_sourcedata/Customers.csv")
sdfCustomers.createOrReplaceTempView("vwCustomers")
display(sdfCustomers)

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

# MAGIC %%sql
# MAGIC DROP TABLE IF EXISTS DIM_CUSTOMERS;
# MAGIC 
# MAGIC CREATE TABLE DIM_CUSTOMERS
# MAGIC AS
# MAGIC SELECT
# MAGIC     CAST(c.customer_key AS INT) AS customer_key,
# MAGIC     CAST(c.Gender AS VARCHAR(10)) AS Gender,
# MAGIC     CAST(c.Name AS VARCHAR(100)) AS Name,
# MAGIC     CAST(c.Address AS VARCHAR(100)) AS Address,
# MAGIC     CAST(c.City AS VARCHAR(100)) AS City,
# MAGIC     CAST(c.State_Code AS VARCHAR(2)) AS State_Code,
# MAGIC     CAST(c.State AS VARCHAR(15)) AS State,
# MAGIC     CAST(c.Zip_Code AS VARCHAR(5)) AS Zip_Code,
# MAGIC     CAST(c.Country_Code AS VARCHAR(2)) AS Country_Code,
# MAGIC     CAST(c.Country AS VARCHAR(15)) AS Country,
# MAGIC     CAST(c.Continent AS VARCHAR(15)) AS Continent,
# MAGIC     CAST(c.Birthday AS VARCHAR(50)) AS Birthday,
# MAGIC     CAST(c.Age AS INT) AS Age
# MAGIC FROM vwCustomers AS c

# METADATA ********************

# META {
# META   "language": "sparksql",
# META   "language_group": "synapse_pyspark"
# META }

# MARKDOWN ********************

# ## Saves the *Product.csv* file as a spark table

# CELL ********************

sdfProducts = spark.read.format("csv").option("header","true").load("Files/semantic_model_sourcedata/Products.csv")
sdfProducts.createOrReplaceTempView("vwProducts")
display(sdfProducts)

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

# MAGIC %%sql
# MAGIC DROP TABLE IF EXISTS dim_products;
# MAGIC 
# MAGIC CREATE TABLE dim_products
# MAGIC AS
# MAGIC SELECT
# MAGIC     CAST(p.product_key AS INT) AS product_key,
# MAGIC     CAST(p.Product_Code AS VARCHAR(50)) AS Product_Code,
# MAGIC     CAST(p.Product_Name AS VARCHAR(100)) AS Product_Name,
# MAGIC     CAST(p.Manufacturer AS VARCHAR(100)) AS Manufacturer,
# MAGIC     CAST(p.Brand AS VARCHAR(100)) AS Brand,
# MAGIC     CAST(p.Color AS VARCHAR(50)) AS Color,
# MAGIC     CAST(p.Weight_Unit_Measure AS VARCHAR(50)) AS Weight_Unit_Measure,
# MAGIC     CAST(p.Weight AS VARCHAR(50)) AS Weight,
# MAGIC     CAST(p.Unit_Cost AS VARCHAR(50)) AS Unit_Cost,
# MAGIC     CAST(p.Unit_Price AS VARCHAR(50)) AS Unit_Price,
# MAGIC     CAST(p.Subcategory_Code AS VARCHAR(50)) AS Subcategory_Code,
# MAGIC     CAST(p.Subcategory AS VARCHAR(50)) AS Subcategory,
# MAGIC     CAST(p.Category_Code AS VARCHAR(50)) AS Category_Code,
# MAGIC     CAST(p.Category AS VARCHAR(50)) AS Category
# MAGIC FROM vwProducts AS p

# METADATA ********************

# META {
# META   "language": "sparksql",
# META   "language_group": "synapse_pyspark"
# META }

# MARKDOWN ********************

# ## Saves the *Store.csv* file as a spark table

# CELL ********************

sdfStores = spark.read.format("csv").option("header","true").load("Files/semantic_model_sourcedata/Stores.csv")
sdfStores.createOrReplaceTempView("vwStores")
display(sdfStores)

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

# MAGIC %%sql
# MAGIC DROP TABLE IF EXISTS dim_stores;
# MAGIC 
# MAGIC CREATE TABLE dim_stores
# MAGIC AS
# MAGIC SELECT
# MAGIC     CAST(s.store_key AS INT) AS store_key,
# MAGIC     CAST(s.Store_Code AS VARCHAR(50)) AS Store_Code,
# MAGIC     CAST(s.Country AS VARCHAR(50)) AS Country,
# MAGIC     CAST(s.State AS VARCHAR(50)) AS State,
# MAGIC     CAST(s.`Name` AS VARCHAR(50)) AS Name,
# MAGIC     CAST(s.Square_Meters AS VARCHAR(50)) AS Square_Meters,
# MAGIC     CAST(s.Open_Date AS VARCHAR(50)) AS Open_Date,
# MAGIC     CAST(s.Close_Date AS VARCHAR(50)) AS Close_Date,
# MAGIC     CAST(s.Status AS VARCHAR(50)) AS Status
# MAGIC FROM vwstores AS s

# METADATA ********************

# META {
# META   "language": "sparksql",
# META   "language_group": "synapse_pyspark"
# META }

# MARKDOWN ********************

# ## Saves the *Sales.csv* file as a spark table

# CELL ********************

sdfSales = spark.read.format("csv").option("header","true").load("Files/semantic_model_sourcedata/Sales.csv")
sdfSales.createOrReplaceTempView("vwSales")
display(sdfSales)

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

# MAGIC %%sql
# MAGIC 
# MAGIC DESCRIBE vwsales

# METADATA ********************

# META {
# META   "language": "sparksql",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

# MAGIC %%sql
# MAGIC DROP TABLE IF EXISTS sales;
# MAGIC 
# MAGIC CREATE TABLE sales
# MAGIC AS
# MAGIC --fix the dates
# MAGIC SELECT
# MAGIC     CAST(f.customer_key AS INT) AS customer_key,
# MAGIC     CAST(f.store_key AS INT) AS store_key,
# MAGIC     CAST(f.product_key AS INT) AS product_key,
# MAGIC     to_date(f.orderdate_key, "M/d/yyyy") AS orderdate_key,
# MAGIC     to_date(f.deliverydate_key, "M/d/yyyy") AS deliverydate_key,
# MAGIC     CAST(f.Price AS INT) AS Price,
# MAGIC     CAST(f.Quantity AS INT) AS Qunatity,
# MAGIC     CAST(f.`Cost` AS INT) AS `Cost`,        
# MAGIC     CAST(f.Fake_Measure_1 AS INT) AS Fake_Measure_1,
# MAGIC     CAST(f.Fake_Measure_2 AS INT) AS Fake_Measure_2,
# MAGIC     CAST(f.Fake_Measure_3 AS INT) AS Fake_Measure_3,
# MAGIC     CAST(f.Fake_Measure_4 AS INT) AS Fake_Measure_4,
# MAGIC     CAST(f.Fake_Measure_5 AS INT) AS Fake_Measure_5,
# MAGIC     CAST(f.Fake_Measure_6 AS INT) AS Fake_Measure_6,
# MAGIC     CAST(f.Fake_Measure_7 AS INT) AS Fake_Measure_7,
# MAGIC     CAST(f.Fake_Measure_8 AS INT) AS Fake_Measure_8,
# MAGIC     CAST(f.Fake_Measure_9 AS INT) AS Fake_Measure_9,
# MAGIC     CAST(f.Fake_Measure_10 AS INT) AS Fake_Measure_10,
# MAGIC     CAST(f.Fake_Measure_11 AS INT) AS Fake_Measure_11,
# MAGIC     CAST(f.Fake_Measure_12 AS INT) AS Fake_Measure_12,
# MAGIC     CAST(f.Fake_Measure_13 AS INT) AS Fake_Measure_13,
# MAGIC     CAST(f.Fake_Measure_14 AS INT) AS Fake_Measure_14,
# MAGIC     CAST(f.Fake_Measure_15 AS INT) AS Fake_Measure_15,
# MAGIC     CAST(f.Fake_Measure_16 AS INT) AS Fake_Measure_16,
# MAGIC     CAST(f.Fake_Measure_17 AS INT) AS Fake_Measure_17,
# MAGIC     CAST(f.Fake_Measure_18 AS INT) AS Fake_Measure_18,
# MAGIC     CAST(f.Fake_Measure_19 AS INT) AS Fake_Measure_19,
# MAGIC     CAST(f.Fake_Measure_20 AS INT) AS Fake_Measure_20,
# MAGIC     CAST(f.Fake_Measure_21 AS INT) AS Fake_Measure_21,
# MAGIC     CAST(f.Fake_Measure_22 AS INT) AS Fake_Measure_22,
# MAGIC     CAST(f.Fake_Measure_23 AS INT) AS Fake_Measure_23,
# MAGIC     CAST(f.Fake_Measure_24 AS INT) AS Fake_Measure_24,
# MAGIC     CAST(f.Fake_Measure_25 AS INT) AS Fake_Measure_25,
# MAGIC     CAST(f.Fake_Measure_26 AS INT) AS Fake_Measure_26,
# MAGIC     CAST(f.Fake_Measure_27 AS INT) AS Fake_Measure_27,
# MAGIC     CAST(f.Fake_Measure_28 AS INT) AS Fake_Measure_28,
# MAGIC     CAST(f.Fake_Measure_29 AS INT) AS Fake_Measure_29,
# MAGIC     CAST(f.Fake_Measure_30 AS INT) AS Fake_Measure_30,
# MAGIC     CAST(f.Fake_Measure_31 AS INT) AS Fake_Measure_31,
# MAGIC     CAST(f.Fake_Measure_32 AS INT) AS Fake_Measure_32,
# MAGIC     CAST(f.Fake_Measure_33 AS INT) AS Fake_Measure_33,
# MAGIC     CAST(f.Fake_Measure_34 AS INT) AS Fake_Measure_34,
# MAGIC     CAST(f.Fake_Measure_35 AS INT) AS Fake_Measure_35,
# MAGIC     CAST(f.Fake_Measure_36 AS INT) AS Fake_Measure_36,
# MAGIC     CAST(f.Fake_Measure_37 AS INT) AS Fake_Measure_37,
# MAGIC     CAST(f.Fake_Measure_38 AS INT) AS Fake_Measure_38,
# MAGIC     CAST(f.Fake_Measure_39 AS INT) AS Fake_Measure_39,
# MAGIC     CAST(f.Fake_Measure_40 AS INT) AS Fake_Measure_40,
# MAGIC     CAST(f.Fake_Measure_41 AS INT) AS Fake_Measure_41,
# MAGIC     CAST(f.Fake_Measure_42 AS INT) AS Fake_Measure_42,
# MAGIC     CAST(f.Fake_Measure_43 AS INT) AS Fake_Measure_43,
# MAGIC     CAST(f.Fake_Measure_44 AS INT) AS Fake_Measure_44,
# MAGIC     CAST(f.Fake_Measure_45 AS INT) AS Fake_Measure_45,
# MAGIC     CAST(f.Fake_Measure_46 AS INT) AS Fake_Measure_46,
# MAGIC     CAST(f.Fake_Measure_47 AS INT) AS Fake_Measure_47,
# MAGIC     CAST(f.Fake_Measure_48 AS INT) AS Fake_Measure_48,
# MAGIC     CAST(f.Fake_Measure_49 AS INT) AS Fake_Measure_49,
# MAGIC     CAST(f.Fake_Measure_50 AS INT) AS Fake_Measure_50,
# MAGIC     CAST(f.Fake_Measure_51 AS INT) AS Fake_Measure_51,
# MAGIC     CAST(f.Fake_Measure_52 AS INT) AS Fake_Measure_52,
# MAGIC     CAST(f.Fake_Measure_53 AS INT) AS Fake_Measure_53,
# MAGIC     CAST(f.Fake_Measure_54 AS INT) AS Fake_Measure_54,
# MAGIC     CAST(f.Fake_Measure_55 AS INT) AS Fake_Measure_55,
# MAGIC     CAST(f.Fake_Measure_56 AS INT) AS Fake_Measure_56,
# MAGIC     CAST(f.Fake_Measure_57 AS INT) AS Fake_Measure_57,
# MAGIC     CAST(f.Fake_Measure_58 AS INT) AS Fake_Measure_58,
# MAGIC     CAST(f.Fake_Measure_59 AS INT) AS Fake_Measure_59,
# MAGIC     CAST(f.Fake_Measure_60 AS INT) AS Fake_Measure_60,
# MAGIC     CAST(f.Fake_Measure_61 AS INT) AS Fake_Measure_61,
# MAGIC     CAST(f.Fake_Measure_62 AS INT) AS Fake_Measure_62,
# MAGIC     CAST(f.Fake_Measure_63 AS INT) AS Fake_Measure_63,
# MAGIC     CAST(f.Fake_Measure_64 AS INT) AS Fake_Measure_64,
# MAGIC     CAST(f.Fake_Measure_65 AS INT) AS Fake_Measure_65,
# MAGIC     CAST(f.Fake_Measure_66 AS INT) AS Fake_Measure_66,
# MAGIC     CAST(f.Fake_Measure_67 AS INT) AS Fake_Measure_67,
# MAGIC     CAST(f.Fake_Measure_68 AS INT) AS Fake_Measure_68,
# MAGIC     CAST(f.Fake_Measure_69 AS INT) AS Fake_Measure_69,
# MAGIC     CAST(f.Fake_Measure_70 AS INT) AS Fake_Measure_70,
# MAGIC     CAST(f.Fake_Measure_71 AS INT) AS Fake_Measure_71,
# MAGIC     CAST(f.Fake_Measure_72 AS INT) AS Fake_Measure_72,
# MAGIC     CAST(f.Fake_Measure_73 AS INT) AS Fake_Measure_73,
# MAGIC     CAST(f.Fake_Measure_74 AS INT) AS Fake_Measure_74,
# MAGIC     CAST(f.Fake_Measure_75 AS INT) AS Fake_Measure_75,
# MAGIC     CAST(f.Fake_Measure_76 AS INT) AS Fake_Measure_76,
# MAGIC     CAST(f.Fake_Measure_77 AS INT) AS Fake_Measure_77,
# MAGIC     CAST(f.Fake_Measure_78 AS INT) AS Fake_Measure_78,
# MAGIC     CAST(f.Fake_Measure_79 AS INT) AS Fake_Measure_79,
# MAGIC     CAST(f.Fake_Measure_80 AS INT) AS Fake_Measure_80,
# MAGIC     CAST(f.Fake_Measure_81 AS INT) AS Fake_Measure_81,
# MAGIC     CAST(f.Fake_Measure_82 AS INT) AS Fake_Measure_82,
# MAGIC     CAST(f.Fake_Measure_83 AS INT) AS Fake_Measure_83,
# MAGIC     CAST(f.Fake_Measure_84 AS INT) AS Fake_Measure_84,
# MAGIC     CAST(f.Fake_Measure_85 AS INT) AS Fake_Measure_85,
# MAGIC     CAST(f.Fake_Measure_86 AS INT) AS Fake_Measure_86,
# MAGIC     CAST(f.Fake_Measure_87 AS INT) AS Fake_Measure_87,
# MAGIC     CAST(f.Fake_Measure_88 AS INT) AS Fake_Measure_88,
# MAGIC     CAST(f.Fake_Measure_89 AS INT) AS Fake_Measure_89,
# MAGIC     CAST(f.Fake_Measure_90 AS INT) AS Fake_Measure_90,
# MAGIC     CAST(f.Fake_Measure_91 AS INT) AS Fake_Measure_91,
# MAGIC     CAST(f.Fake_Measure_92 AS INT) AS Fake_Measure_92,
# MAGIC     CAST(f.Fake_Measure_93 AS INT) AS Fake_Measure_93,
# MAGIC     CAST(f.Fake_Measure_94 AS INT) AS Fake_Measure_94,
# MAGIC     CAST(f.Fake_Measure_95 AS INT) AS Fake_Measure_95,
# MAGIC     CAST(f.Fake_Measure_96 AS INT) AS Fake_Measure_96,
# MAGIC     CAST(f.Fake_Measure_97 AS INT) AS Fake_Measure_97,
# MAGIC     CAST(f.Fake_Measure_98 AS INT) AS Fake_Measure_98,
# MAGIC     CAST(f.Fake_Measure_99 AS INT) AS Fake_Measure_99,
# MAGIC     CAST(f.Fake_Measure_100 AS INT) AS Fake_Measure_100
# MAGIC FROM vwsales AS f

# METADATA ********************

# META {
# META   "language": "sparksql",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

# MAGIC %%sql
# MAGIC 
# MAGIC OPTIMIZE dim_dates;
# MAGIC OPTIMIZE dim_customers;
# MAGIC OPTIMIZE dim_products;
# MAGIC OPTIMIZE dim_stores;
# MAGIC OPTIMIZE sales;

# METADATA ********************

# META {
# META   "language": "sparksql",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************


# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }
