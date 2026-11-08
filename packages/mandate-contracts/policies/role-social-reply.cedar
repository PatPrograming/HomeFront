forbid(principal, action == Mandate::Action::"social_reply", resource)
  unless { principal has roleTools && principal.roleTools.contains("social_reply") };
