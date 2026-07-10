{{ config(materialized='table', schema='gold') }}

-- Transaction-grain fact table, denormalized with the customer/franchise
-- attributes BI tools most commonly filter and group by.

with transactions as (

    select * from {{ ref('silver_transactions') }}

),

customers as (

    select * from {{ ref('silver_customers') }}

),

franchises as (

    select * from {{ ref('silver_franchises') }}

)

select
    t.transaction_id,
    t.transaction_date,
    t.transaction_ts,
    t.customer_id,
    c.full_name as customer_name,
    c.country as customer_country,
    t.franchise_id,
    f.franchise_name,
    f.city as franchise_city,
    f.country as franchise_country,
    t.product,
    t.quantity,
    t.unit_price,
    t.total_price,
    t.payment_method,
    t.has_price_mismatch
from transactions t
left join customers c on t.customer_id = c.customer_id
left join franchises f on t.franchise_id = f.franchise_id
