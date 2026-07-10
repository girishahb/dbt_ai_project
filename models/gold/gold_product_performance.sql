{{ config(materialized='table', schema='gold') }}

-- Product-level sales performance and revenue mix across the whole chain.

with sales as (

    select * from {{ ref('gold_fact_sales') }}

),

product_agg as (

    select
        product,
        count(distinct transaction_id) as total_orders,
        sum(quantity) as total_units_sold,
        sum(total_price) as total_revenue,
        round(avg(unit_price), 2) as avg_unit_price,
        count(distinct franchise_id) as franchises_selling,
        count(distinct customer_id) as unique_customers
    from sales
    group by 1

)

select
    product,
    total_orders,
    total_units_sold,
    total_revenue,
    avg_unit_price,
    franchises_selling,
    unique_customers,
    round(total_revenue * 100.0 / sum(total_revenue) over (), 2) as pct_of_total_revenue,
    rank() over (order by total_revenue desc) as revenue_rank
from product_agg
order by total_revenue desc
