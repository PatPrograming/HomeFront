forbid(principal, action == Mandate::Action::"usaspending_search", resource)
  unless { principal has role && ["chief_of_staff", "legislative_aide"].contains(principal.role) };
