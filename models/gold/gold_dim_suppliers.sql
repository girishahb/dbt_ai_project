{{ config(materialized='table', schema='gold') }}

select
    supplier_id,
    supplier_name,
    ingredient,
    continent,
    city,
    district,
    supplier_size,
    is_approved,
    latitude,
    longitude
from {{ ref('silver_suppliers') }}
