{{ config(materialized='view', schema='silver') }}

with source as (

    select * from {{ source('bakehouse', 'sales_suppliers') }}

)

select
    supplierID as supplier_id,
    trim(name) as supplier_name,
    lower(trim(ingredient)) as ingredient,
    initcap(trim(continent)) as continent,
    initcap(trim(city)) as city,
    initcap(trim(district)) as district,
    upper(trim(size)) as supplier_size,
    longitude,
    latitude,
    case
        when upper(trim(approved)) = 'Y' then true
        else false
    end as is_approved
from source
where supplierID is not null
