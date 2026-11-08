forbid(principal, action == Mandate::Action::"noaa_climate", resource)
  unless { principal has roleTools && principal.roleTools.contains("noaa_climate") };
