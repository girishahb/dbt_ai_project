{{ config(materialized='table', schema='gold') }}

-- Customer lifetime value with a simple recency-based status and a value
-- segment, useful for retention/marketing targeting.
--
-- Thresholds below are reasonable starting assumptions for this dataset and
-- should be revisited with the business once real purchase cadence is known:
--   * Active:    purchased within the last 30 days
--   * At Risk:   purchased 31-90 days ago
--   * Churned:   no purchase in 90+ days
--   * High Value:   lifetime spend >= 200
--   * Medium Value: lifetime spend >= 75
--   * Low Value:    lifetime spend < 75

with sales as (

    select * from {{ ref('gold_fact_sales') }}

),

agg as (

    select
        customer_id,
        customer_name,
        customer_country,
        min(transaction_date) as first_purchase_date,
        max(transaction_date) as last_purchase_date,
        count(distinct transaction_id) as total_orders,
        sum(total_price) as lifetime_value,
        round(sum(total_price) / count(distinct transaction_id), 2) as avg_order_value,
        datediff(current_date(), max(transaction_date)) as days_since_last_purchase
    from sales
    group by 1, 2, 3

)

select
    customer_id,
    customer_name,
    customer_country,
    first_purchase_date,
    last_purchase_date,
    total_orders,
    lifetime_value,
    avg_order_value,
    days_since_last_purchase,
    case
        when days_since_last_purchase <= 30 then 'Active'
        when days_since_last_purchase <= 90 then 'At Risk'
        else 'Churned'
    end as customer_status,
    case
        when lifetime_value >= 200 then 'High Value'
        when lifetime_value >= 75 then 'Medium Value'
        else 'Low Value'
    end as value_segment
from agg
