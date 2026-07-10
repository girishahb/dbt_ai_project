{{ config(materialized='table', schema='gold') }}

select
    customer_id,
    full_name,
    first_name,
    last_name,
    email_address,
    phone_number,
    city,
    state,
    country,
    continent,
    postal_zip_code,
    gender
from {{ ref('silver_customers') }}
