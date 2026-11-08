forbid(principal, action == Mandate::Action::"web_scrape", resource)
  unless { principal has roleTools && principal.roleTools.contains("web_scrape") };
