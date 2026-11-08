permit(principal, action in [Mandate::Action::"inbox_read", Mandate::Action::"datagov_search",
                             Mandate::Action::"web_scrape"], resource)
  when { context.delegatorActive };
