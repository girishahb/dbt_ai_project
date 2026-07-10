{{ config(materialized='table', schema='gold') }}

-- Denormalized franchise dimension: each franchise enriched with its primary
-- ingredient supplier, so BI tools can slice franchises by supplier attributes
-- without an extra join.

with franchises as (

    select * from {{ ref('silver_franchises') }}

),

suppliers as (

    select * from {{ ref('silver_suppliers') }}

)

select
    f.franchise_id,
    f.franchise_name,
    f.city,
    f.district,
    f.zipcode,
    f.country,
    f.franchise_size,
    f.latitude,
    f.longitude,
    s.supplier_id,
    s.supplier_name,
    s.ingredient as primary_ingredient,
    s.is_approved as supplier_is_approved
from franchises f
left join suppliers s
    on f.supplier_id = s.supplier_id
