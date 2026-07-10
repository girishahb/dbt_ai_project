{{ config(materialized='table', schema='gold') }}

-- Monthly performance scorecard per franchise: revenue, volume, order economics,
-- and each franchise's best-selling product for the month.

with sales as (

    select * from {{ ref('gold_fact_sales') }}

),

monthly as (

    select
        franchise_id,
        franchise_name,
        franchise_city,
        franchise_country,
        date_trunc('month', transaction_date) as sales_month,
        sum(total_price) as total_revenue,
        sum(quantity) as total_units_sold,
        count(distinct transaction_id) as total_orders,
        count(distinct customer_id) as unique_customers,
        round(sum(total_price) / count(distinct transaction_id), 2) as avg_order_value
    from sales
    group by 1, 2, 3, 4, 5

),

product_monthly as (

    select
        franchise_id,
        date_trunc('month', transaction_date) as sales_month,
        product,
        sum(total_price) as product_revenue
    from sales
    group by 1, 2, 3

),

product_ranks as (

    select
        franchise_id,
        sales_month,
        product,
        row_number() over (
            partition by franchise_id, sales_month
            order by product_revenue desc
        ) as rn
    from product_monthly

)

select
    m.franchise_id,
    m.franchise_name,
    m.franchise_city,
    m.franchise_country,
    m.sales_month,
    m.total_revenue,
    m.total_units_sold,
    m.total_orders,
    m.unique_customers,
    m.avg_order_value,
    p.product as top_product,
    rank() over (partition by m.sales_month order by m.total_revenue desc) as revenue_rank_in_month
from monthly m
left join product_ranks p
    on m.franchise_id = p.franchise_id
    and m.sales_month = p.sales_month
    and p.rn = 1
