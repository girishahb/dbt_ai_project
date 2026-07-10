{% macro generate_schema_name(custom_schema_name, node) -%}
    {#-
        Medallion layout: silver/gold models declare +schema: silver / +schema: gold
        and land in that schema directly (e.g. ai_project.silver, ai_project.gold)
        instead of the dbt default of "<target_schema>_<custom_schema>".
    -#}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
