forbid(principal, action == Mandate::Action::"social_post", resource)
  unless { principal has roleTools && principal.roleTools.contains("social_post") };
