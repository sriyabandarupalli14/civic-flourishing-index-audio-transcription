"""Enforce limits using the installed faster-whisper tokenizer and prompt builder."""


def fit_hotwords(terms, encode, budget):
    accepted = []
    for term in terms:
        candidate = ", ".join([*accepted, term])
        if len(encode(" " + candidate)) > budget:
            break
        accepted.append(term)
    text = ", ".join(accepted)
    return text or None, {"requested_terms": len(terms), "selected_terms": accepted,
                          "hotword_tokens": len(encode(" " + text)) if text else 0,
                          "hotword_budget": budget, "truncated": len(terms) != len(accepted)}


def prepare_prompt(model, decoding, terms, budget):
    from faster_whisper.tokenizer import Tokenizer

    tokenizer = Tokenizer(model.hf_tokenizer, model.model.is_multilingual,
                          decoding.get("task", "transcribe"), decoding.get("language", "en"))
    native_limit = model.max_length // 2 - 1
    if not 1 <= budget <= native_limit or decoding.get("prefix"):
        raise ValueError("Prompt budget exceeds decoder limits or prefix conflicts with hotwords")
    hotwords, stats = fit_hotwords(terms, tokenizer.encode, budget)
    initial = decoding.get("initial_prompt")
    context = tokenizer.encode(" " + initial.strip()) if initial else []
    generated = model.get_prompt(tokenizer, context, without_timestamps=decoding.get("without_timestamps", False),
                                 hotwords=hotwords)
    if len(context) > native_limit or len(generated) > model.max_length // 2:
        raise ValueError("Initial context plus hotwords uses too many prompt tokens")
    return hotwords, {**stats, "native_hotword_limit": native_limit, "context_tokens": len(context),
                      "first_window_prompt_tokens": len(generated)}
