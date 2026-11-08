permit(principal, action == Mandate::Action::"social_reply", resource)
  when { context.delegatorActive && context.account == "official" };
