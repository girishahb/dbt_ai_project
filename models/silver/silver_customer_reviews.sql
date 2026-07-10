{{ config(materialized='view', schema='silver') }}

with source as (

    select * from {{ source('bakehouse', 'media_customer_reviews') }}

),

cleaned as (

    select
        new_id as review_id,
        franchiseID as franchise_id,
        review_date,
        trim(review) as review_text,
        length(trim(review)) as review_char_length,
        -- many reviews are written as free text with an embedded "x/5" or "x.x/5" star rating
        regexp_extract(review, '([0-5](?:[.][0-9])?)[ ]*/[ ]*5', 1) as extracted_rating_raw
    from source
    where new_id is not null

)

select
    review_id,
    franchise_id,
    review_date,
    review_text,
    review_char_length,
    case
        when extracted_rating_raw != '' then cast(extracted_rating_raw as double)
        else null
    end as star_rating,
    case
        when extracted_rating_raw != '' and cast(extracted_rating_raw as double) >= 4 then 'Positive'
        when extracted_rating_raw != '' and cast(extracted_rating_raw as double) >= 2.5 then 'Neutral'
        when extracted_rating_raw != '' then 'Negative'
        else 'Unrated'
    end as sentiment_bucket
from cleaned
