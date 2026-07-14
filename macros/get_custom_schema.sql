{% macro generate_schema_name(custom_schema_name, node) -%}
    {#-
        Medallion layout: silver/gold models declare +schema: silver / +schema: gold
        and land in that schema directly (e.g. ai_project.silver, ai_project.gold)
        instead of the dbt default of "<target_schema>_<custom_schema>".

        Exception: the `ci` target (profiles/profiles.yml) is the self-heal
        agent's scratch validation target (see agent/README.md). Without this
        exception, +schema overrides would make *every* target -- including
        ci -- resolve silver/gold models straight to the real
        ai_project.silver / ai_project.gold schemas, so a candidate fix built
        under the ci target would land right on top of prod data. For that
        target only, fall back to dbt's normal "<target_schema>_<custom>"
        behavior so runs land in agent_ci_silver / agent_ci_gold instead.
    -#}
    {%- if target.name == 'ci' -%}
        {%- if custom_schema_name is none -%}
            {{ target.schema }}
        {%- else -%}
            {{ target.schema }}_{{ custom_schema_name | trim }}
        {%- endif -%}
    {%- elif custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
