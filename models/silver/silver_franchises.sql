{{ config(materialized='view', schema='silver') }}

with source as (

    select * from {{ source('bakehouse', 'sales_franchises') }}

)

select
    franchiseID as franchise_id,
    trim(name) as franchise_name,
    initcap(trim(city)) as city,
    initcap(trim(district)) as district,
    trim(zipcode) as zipcode,
    initcap(trim(country)) as country,
    upper(trim(size)) as franchise_size,
    longitude,
    latitude,
    supplierID as supplier_id
from source
where franchiseID is not null
