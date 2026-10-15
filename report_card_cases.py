"""
The exam Moss sits for its report card (report_card.py).

Each message lists `want`: the tools that would be a right first move (ANY of them
counts; e.g. ticking a task may start by looking the task up). `want=[]` means no
tool at all (small talk, general questions). `need_all=True`: a message with two
jobs, where every listed tool is needed. `after`: Moss's previous message, for
follow-ups like "yes please".

The messages are made up (in the owner's mix of English and Malay), so this file
holds nothing private.
"""

LOOKUP_TASK = ["complete_task", "list_tasks"]

CASES = [
    # --- Logging: food, drinks, water, body -------------------------------------------
    {"say": "had nasi lemak ayam for lunch", "want": ["log_entry"]},
    {"say": "breakfast roti canai 2 keping with teh tarik", "want": ["log_entry"]},
    {"say": "minum air 500ml", "want": ["log_entry"]},
    {"say": "drank a big bottle of water just now, 1 litre", "want": ["log_entry"]},
    {"say": "iced latte from zus oat milk", "want": ["log_entry"]},
    {"say": "dinner: chicken chop with fries and coleslaw", "want": ["log_entry"]},
    {"say": "makan kuih lapis 3 potong petang tadi", "want": ["log_entry"]},
    {"say": "took my vitamin D", "want": ["log_entry"]},
    {"say": "weighed 54.6kg this morning", "want": ["log_entry"]},
    {"say": "slept 6 hours only last night", "want": ["log_entry"]},
    {"say": "brisk walk 3.2km 40 min", "want": ["log_entry"]},
    {"say": "gym today: squat 3x8 40kg, hip thrust 3x10 60kg, 1 hour", "want": ["log_entry"]},
    {"say": "pilates class 50 minutes", "want": ["log_entry"]},
    {"say": "feeling super stressed today, deadline banyak", "want": ["log_entry"]},
    {"say": "period started today", "want": ["log_entry"]},
    {"say": "cramps teruk hari ni", "want": ["log_entry"]},
    {"say": "finished week 3 of the google cloud course", "want": ["log_entry"]},
    # --- Logging: money -------------------------------------------------------------
    {"say": "grab to office rm18", "want": ["log_entry"]},
    {"say": "spent rm45 at watsons, sunscreen and lip balm", "want": ["log_entry"]},
    {"say": "rm12.50 nasi kerabu", "want": ["log_entry"]},
    {"say": "bought a phone case on shopee rm19.90", "want": ["log_entry"]},
    {"say": "movie with friends rm24", "want": ["log_entry"]},
    {"say": "spent 20", "want": []},  # on what? Moss should ask first
    # --- Corrections (need the earlier message) ---------------------------------------
    {"after": "🍛 Nasi lemak ayam · ~650 kcal ✅ Logged", "say": "actually i only ate half",
     "want": ["update_entry", "find_entries"]},
    {"after": "✅ Logged RM18 Grab (transport)", "say": "oops that was yesterday not today",
     "want": ["update_entry", "find_entries"]},
    {"after": "🧋 Brown sugar boba · ~380 kcal ✅ Logged", "say": "undo that, i didnt drink it",
     "want": ["delete_entry", "find_entries"]},
    # --- Looking things up -------------------------------------------------------------
    {"say": "how much did i spend this week?", "want": ["summarise", "find_entries"]},
    {"say": "what did i eat yesterday", "want": ["find_entries", "summarise"]},
    {"say": "berapa kalori hari ni setakat ni?", "want": ["summarise", "find_entries"]},
    {"say": "how many times did i go to the gym this month", "want": ["summarise", "find_entries"]},
    {"say": "am i drinking enough water lately?", "want": ["summarise", "find_entries", "get_goals"]},
    {"say": "when is my next period?", "want": ["cycle_status"]},
    {"say": "is my period late?", "want": ["cycle_status"]},
    {"say": "how much budget left for food", "want": ["get_budgets"]},
    {"say": "what are my goals again", "want": ["get_goals"]},
    {"say": "how much did i spend on grab last month", "want": ["summarise", "find_entries"]},
    # --- Tasks and reminders ----------------------------------------------------------------
    {"say": "remind me to call mama at 3pm", "want": ["add_task"]},
    {"say": "add buy birthday gift for aina by saturday", "want": ["add_task"]},
    {"say": "skincare every night at 10pm", "want": ["add_task"]},
    {"say": "remind me to submit the assignment tomorrow 9am", "want": ["add_task"]},
    {"say": "done with the laundry!", "want": LOOKUP_TASK},
    {"say": "dah siap assignment", "want": LOOKUP_TASK},
    {"say": "what's on my to do list", "want": ["list_tasks"]},
    {"say": "move the dentist task to next monday", "want": ["update_task", "list_tasks"]},
    {"say": "delete the task about the gift", "want": ["delete_task", "list_tasks"]},
    {"say": "i need to: renew passport, reply email, clean room, book flight",
     "want": ["add_task"]},
    # --- Calendar -------------------------------------------------------------------------
    {"say": "python class every thursday 2-4pm until december", "want": ["add_calendar_event"]},
    {"say": "dentist appointment on 22 oct at 10am", "want": ["add_calendar_event"]},
    {"say": "aina's birthday is on 3 november", "want": ["add_calendar_event"]},
    {"say": "what's on my calendar tomorrow", "want": ["list_calendar_events"]},
    {"say": "am i free friday afternoon?", "want": ["list_calendar_events"]},
    {"say": "move my dentist appointment to 24 oct same time",
     "want": ["list_calendar_events", "update_calendar_event"]},
    {"say": "cancel the meeting on thursday", "want": ["list_calendar_events", "delete_calendar_event"]},
    # --- Bills and subscriptions ---------------------------------------------------------
    {"say": "spotify rm16.90 every 15th", "want": ["add_bill"]},
    {"say": "TNB bill around rm80 due on the 20th", "want": ["add_bill"]},
    {"say": "paid my tnb, rm92", "want": ["mark_bill_paid", "list_bills"]},
    {"say": "what bills are due this month", "want": ["list_bills"]},
    {"say": "i cancelled netflix", "want": ["delete_bill", "list_bills"]},
    {"say": "youtube premium went up to rm20.90", "want": ["update_bill", "list_bills"]},
    # --- Goals, budgets, buttons, weather ----------------------------------------------------
    {"say": "my water goal is 2.5 litres a day", "want": ["set_goal"]},
    {"say": "set my food budget to rm400 a month", "want": ["set_budget"]},
    {"say": "i want to aim for 8000 steps daily", "want": ["set_goal"]},
    {"say": "add a quick button for vitamin D", "want": ["add_quick_button"]},
    {"say": "remove the collagen button", "want": ["remove_quick_button", "list_quick_buttons"]},
    {"say": "i moved, i live in shah alam now", "want": ["set_weather_place"]},
    {"say": "send me storm alerts for petaling jaya and subang", "want": ["set_rain_alert_areas"]},
    # --- Work and study write-ups ----------------------------------------------------------
    {"say": "today i fixed the login bug and wrote tests for the api", "want": ["log_entry"]},
    {"say": "write my log book entry for today", "want": ["work_notes"]},
    {"say": "draft my weekly report for my supervisor", "want": ["work_notes"]},
    {"after": "📒 Log book report · What did you work on today?",
     "say": "cleaned the dataset and trained the first model", "want": ["log_entry", "work_notes"]},
    {"say": "my log book format is: Date, Task, Outcome, Problems", "want": ["set_draft_style"]},
    # --- Second brain -------------------------------------------------------------------------
    {"say": "remember that my gym locker code is 2207", "want": ["save_note"]},
    {"say": "save this https://example.com/how-rag-works", "want": ["save_link"]},
    {"say": "https://example.com/xgboost-tutorial save for later", "want": ["save_link"]},
    {"say": "note: the printer on level 2 needs the colour code 7741", "want": ["save_note"]},
    {"say": "what's my gym locker code?", "want": ["search_brain"]},
    {"say": "what did that rag article say about chunking", "want": ["search_brain"]},
    {"say": "explain SHAP from my notes", "want": ["search_brain"]},
    {"say": "what have i saved so far", "want": ["list_brain"]},
    {"say": "forget the printer note", "want": ["search_brain", "list_brain", "forget_brain_item"]},
    {"say": "in my lecture notes, what are the 3 types of machine learning?", "want": ["search_brain"]},
    # --- Small talk and general help: no tools ---------------------------------------------------
    {"say": "thank you moss!", "want": []},
    {"say": "good morning 🌸", "want": []},
    {"say": "hehe okay", "want": []},
    {"say": "you're so cute", "want": []},
    {"say": "what's the difference between AI and machine learning?", "want": []},
    {"say": "give me a short motivational quote", "want": []},
    {"say": "how do i say thank you in japanese", "want": []},
    {"say": "any tips to sleep earlier?", "want": []},
    {"after": "✅ Logged · today: 1,430 kcal, 1.25 L water", "say": "thanks!", "want": []},
    {"after": "⏰ Reminder: 🧴 Skincare", "say": "ok later", "want": []},
    # --- Two jobs in one message -------------------------------------------------------------------
    {"say": "roti canai and teh o ais, and remind me to pay the bill at 8pm",
     "want": ["log_entry", "add_task"], "need_all": True},
    {"say": "spent rm30 on lunch with aina, also add dinner with family sunday 7pm to my calendar",
     "want": ["log_entry", "add_calendar_event"], "need_all": True},
    {"say": "drank 500ml water and done with my skincare",
     "want": ["log_entry", "complete_task", "list_tasks"]},
    # --- Follow-ups ----------------------------------------------------------------------------
    {"after": "Want me to add Python class to your calendar every Thursday 2-4pm? 📅",
     "say": "yes please", "want": ["add_calendar_event"]},
    {"after": "Should I add these as daily tasks: stretch, read, make bed? 🎀",
     "say": "yes all", "want": ["add_task"]},
]

# --- Second brain: a pretend set of saved things, and questions about them ------------------
# expect = the title that should come back first, or None: nothing saved answers it.
BRAIN_DOCS = {
    "SHAP notes": "SHAP (SHapley Additive exPlanations) explains a single prediction. Each feature gets a "
                  "SHAP value: how much it pushed the prediction up or down from the average prediction. "
                  "Positive values raise the risk, negative values lower it. A waterfall plot shows the "
                  "features for one person; a summary (beeswarm) plot shows them across the whole dataset. "
                  "SHAP values come from game theory: Shapley values share a payout fairly between players.",
    "XGBoost notes": "XGBoost builds decision trees one after another. Each new tree learns from the errors "
                     "of the trees before it (gradient boosting). Important settings: learning_rate (how big "
                     "each step is), max_depth (how deep each tree grows), n_estimators (how many trees). "
                     "Early stopping stops adding trees when the validation score stops improving. "
                     "scale_pos_weight helps when one class is much rarer than the other.",
    "How RAG works": "Retrieval-augmented generation (RAG) lets a language model answer from your own "
                     "documents. Documents are split into chunks, usually a few hundred words with some "
                     "overlap so sentences are not cut in half. Each chunk is turned into an embedding and "
                     "stored in a vector database. At question time the question is embedded too, the "
                     "nearest chunks are retrieved, and the model answers using them, which reduces "
                     "hallucinations and allows citations.",
    "Docker basics": "Docker packages an app with everything it needs into an image. A Dockerfile lists the "
                     "steps: start FROM a base image, COPY the code, RUN pip install, and set the CMD that "
                     "starts the app. A container is a running image. Cloud Run runs containers and scales "
                     "them to zero when there is no traffic.",
    "Gym locker": "My gym locker code is 2207. Locker number 41 near the pilates studio.",
    "Office printer": "The printer on level 2 needs the colour code 7741. Black and white printing needs no "
                      "code. Scan to email is on the touch screen.",
    "Detox juice recipe": "Detox juice: 1 green apple, half a cucumber, a handful of spinach, half a lemon, "
                          "a thumb of ginger and 200ml cold water. Blend and drink straight away.",
    "Machine learning lecture 1": "There are three main types of machine learning. Supervised learning "
                                  "learns from labelled examples (classification, regression). Unsupervised "
                                  "learning finds patterns without labels (clustering). Reinforcement "
                                  "learning learns by trial and error from rewards.",
}

BRAIN_QUESTIONS = [
    {"ask": "how does SHAP explain a prediction?", "expect": "SHAP notes"},
    {"ask": "what does a waterfall plot show", "expect": "SHAP notes"},
    {"ask": "where do shapley values come from", "expect": "SHAP notes"},
    {"ask": "what does max_depth do in xgboost", "expect": "XGBoost notes"},
    {"ask": "how do i handle imbalanced classes in my boosting model", "expect": "XGBoost notes"},
    {"ask": "what is early stopping", "expect": "XGBoost notes"},
    {"ask": "why do chunks overlap", "expect": "How RAG works"},
    {"ask": "how does RAG reduce hallucinations", "expect": "How RAG works"},
    {"ask": "what goes in a dockerfile", "expect": "Docker basics"},
    {"ask": "does cloud run cost money when nobody uses it", "expect": "Docker basics"},
    {"ask": "what's my gym locker code", "expect": "Gym locker"},
    {"ask": "which locker is mine", "expect": "Gym locker"},
    {"ask": "code for colour printing", "expect": "Office printer"},
    {"ask": "how do i scan to email at the office", "expect": "Office printer"},
    {"ask": "what goes in my detox juice", "expect": "Detox juice recipe"},
    {"ask": "how much ginger for the green juice", "expect": "Detox juice recipe"},
    {"ask": "what are the three types of machine learning", "expect": "Machine learning lecture 1"},
    {"ask": "what is clustering", "expect": "Machine learning lecture 1"},
    # Nothing saved answers these, even if they sound close:
    {"ask": "what's my wifi password at home", "expect": None},
    {"ask": "recipe for chocolate cake", "expect": None},
    {"ask": "when is my car insurance due", "expect": None},
    {"ask": "what's my bank account number", "expect": None},
    {"ask": "how do i fix a flat tyre", "expect": None},
    {"ask": "what did the doctor say about my blood test", "expect": None},
    {"ask": "how to make teh tarik", "expect": None},
    {"ask": "what's the capital of japan", "expect": None},
]
