permit(principal, action in [Mandate::Action::"census_query", Mandate::Action::"openfda_search",
                             Mandate::Action::"eia_query", Mandate::Action::"nasa_search",
                             Mandate::Action::"noaa_climate", Mandate::Action::"nps_parks",
                             Mandate::Action::"bls_series", Mandate::Action::"fec_search",
                             Mandate::Action::"epa_search", Mandate::Action::"fcc_search",
                             Mandate::Action::"usaspending_search", Mandate::Action::"sam_entity_lookup"], resource)
  when { context.delegatorActive };
