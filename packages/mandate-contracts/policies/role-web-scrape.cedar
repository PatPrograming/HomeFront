forbid(principal, action == Mandate::Action::"web_scrape", resource)
  unless { principal has role && ["chief_of_staff", "caseworker", "communications_director", "legislative_aide", "intern"].contains(principal.role) };
