forbid(principal, action == Mandate::Action::"nps_parks", resource)
  unless { principal has roleTools && principal.roleTools.contains("nps_parks") };
