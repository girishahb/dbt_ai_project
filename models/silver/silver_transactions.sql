{{ config(materialized='view', schema='silver') }}

with source as (

    select * from {{ source('bakehouse', 'sales_transactions') }}

),

cleaned as (

    select
        transactionID as transaction_id,
        customerID as customer_id,
        franchiseID as franchise_id,
        dateTime as transaction_ts,
        date(dateTime) as transaction_date,
        trim(product) as product,
        quantity,
        unitPrice as unit_price,
        totalPrice as total_price,
        (quantity * unitPrice) as expected_total_price,
        lower(trim(paymentMethod)) as payment_method,
        -- PCI hygiene: never carry full card numbers past the silver layer
        concat('**** **** **** ', substring(cast(cardNumber as string), -4)) as card_number_masked
    from source
    where transactionID is not null
      and customerID is not null
      and franchiseID is not null
      and quantity > 0
      and unitPrice >= 0

)

select
    transaction_id,
    customer_id,
    franchise_id,
    transaction_ts,
    transaction_date,
    product,
    quantity,
    unit_price,
    total_price,
    payment_method,
    card_number_masked,
    -- flags rows where totalPrice doesn't reconcile with quantity * unitPrice
    case
        when total_price <> expected_total_price then true
        else false
    end as has_price_mismatch
from cleaned
