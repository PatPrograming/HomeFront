forbid(principal, action == Mandate::Action::"bls_series", resource)
  unless { principal has roleTools && principal.roleTools.contains("bls_series") };
