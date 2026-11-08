permit(principal, action == Mandate::Action::"social_post", resource)
  when { context.delegatorActive && context.account == "official" };
