{{ config(materialized='table', schema='gold') }}

-- Reputation scorecard per franchise, blending parsed star ratings with a
-- simple sentiment breakdown. Franchises with no reviews yet are still
-- included (left join) so the mart can be used for coverage reporting too.

with reviews as (

    select * from {{ ref('silver_customer_reviews') }}

),

franchises as (

    select * from {{ ref('silver_franchises') }}

),

review_agg as (

    select
        franchise_id,
        count(*) as total_reviews,
        round(avg(star_rating), 2) as avg_star_rating,
        sum(case when sentiment_bucket = 'Positive' then 1 else 0 end) as positive_reviews,
        sum(case when sentiment_bucket = 'Neutral' then 1 else 0 end) as neutral_reviews,
        sum(case when sentiment_bucket = 'Negative' then 1 else 0 end) as negative_reviews,
        sum(case when sentiment_bucket = 'Unrated' then 1 else 0 end) as unrated_reviews
    from reviews
    group by 1

)

select
    f.franchise_id,
    f.franchise_name,
    f.city,
    f.country,
    coalesce(r.total_reviews, 0) as total_reviews,
    r.avg_star_rating,
    coalesce(r.positive_reviews, 0) as positive_reviews,
    coalesce(r.neutral_reviews, 0) as neutral_reviews,
    coalesce(r.negative_reviews, 0) as negative_reviews,
    coalesce(r.unrated_reviews, 0) as unrated_reviews,
    case
        when r.avg_star_rating >= 4 then 'Strong Reputation'
        when r.avg_star_rating >= 2.5 then 'Mixed Reputation'
        when r.avg_star_rating is not null then 'Poor Reputation'
        else 'No Reviews'
    end as reputation_tier
from franchises f
left join review_agg r on f.franchise_id = r.franchise_id
