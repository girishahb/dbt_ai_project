{{ config(materialized='view', schema='silver') }}

with source as (

    select * from {{ source('bakehouse', 'sales_customers') }}

),

cleaned as (

    select
        customerID as customer_id,
        initcap(trim(first_name)) as first_name,
        initcap(trim(last_name)) as last_name,
        concat(initcap(trim(first_name)), ' ', initcap(trim(last_name))) as full_name,
        lower(trim(email_address)) as email_address,
        trim(phone_number) as phone_number,
        trim(address) as address,
        initcap(trim(city)) as city,
        initcap(trim(state)) as state,
        initcap(trim(country)) as country,
        initcap(trim(continent)) as continent,
        postal_zip_code,
        case
            when lower(trim(gender)) in ('female', 'f') then 'Female'
            when lower(trim(gender)) in ('male', 'm') then 'Male'
            else 'Unknown'
        end as gender,
        customer_loyalty_score,
        this_column_does_not_exist
    from source
    where customerID is not null

),

deduped as (

    select
        *,
        row_number() over (partition by customer_id order by customer_id) as rn
    from cleaned

)

select
    customer_id,
    first_name,
    last_name,
    full_name,
    email_address,
    phone_number,
    address,
    city,
    state,
    country,
    continent,
    postal_zip_code,
    gender,
    customer_loyalty_score
from deduped
where rn = 1
