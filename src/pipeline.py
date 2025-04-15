import os
import pathlib
import requests
import json
import re
from bs4 import BeautifulSoup
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from langchain.schema import HumanMessage, AIMessage
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Literal, Annotated
from typing_extensions import TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
import sib_api_v3_sdk
from sib_api_v3_sdk.rest import ApiException

# Load environment variables
from dotenv import load_dotenv

load_dotenv()

PROMPT_DIR = "prompts"


class ResultRelevance(BaseModel):
    explanation: str
    id: str


class RelevanceCheckOutput(BaseModel):
    relevant_results: List[ResultRelevance]


def load_prompt(prompt_name):
    with open(os.path.join(PROMPT_DIR, f"{prompt_name}.md"), "r") as file:
        return file.read()


def search_serper(search_query):
    url = "https://google.serper.dev/search"

    payload = json.dumps({"q": search_query, "gl": "gb", "num": 30, "tbs": "qdr:d"})

    headers = {
        "X-API-KEY": "58670c52d6dbd47c4c094dd01556874d28ea3e6e",
        "Content-Type": "application/json",
    }

    response = requests.request("POST", url, headers=headers, data=payload)
    results = json.loads(response.text)
    results_list = results.get("organic", [])

    all_results = []
    for id, result in enumerate(results_list, 1):
        result_dict = {
            "title": result["title"],
            "link": result["link"],
            "snippet": result["snippet"],
            "search_term": search_query,
            "id": id,
        }
        all_results.append(result_dict)
    return all_results


def check_search_relevance(results):
    prompt = load_prompt("relevance_check")
    llm = ChatOpenAI(model="gpt-4o").with_structured_output(RelevanceCheckOutput)
    prompt_template = ChatPromptTemplate.from_messages([("system", prompt)])
    return (prompt_template | llm).invoke({"input_search_results": results})


def convert_html_to_markdown(html):
    soup = BeautifulSoup(html, "html.parser")
    for h in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        h.replace_with(f"{'#'*int(h.name[1])} {h.get_text()}\n\n")
    for tag, fmt in [
        ("a", "[{0}]({1})"),
        ("b", "**{0}**"),
        ("strong", "**{0}**"),
        ("i", "*{0}*"),
        ("em", "*{0}*"),
    ]:
        for t in soup.find_all(tag):
            val = (
                fmt.format(t.get_text(), t.get("href", ""))
                if tag == "a"
                else fmt.format(t.get_text())
            )
            t.replace_with(val)
    for ul in soup.find_all("ul"):
        for li in ul.find_all("li"):
            li.replace_with(f"- {li.get_text()}\n")
    for ol in soup.find_all("ol"):
        for i, li in enumerate(ol.find_all("li"), 1):
            li.replace_with(f"{i}. {li.get_text()}\n")
    return re.sub(r"\n\s*\n", "\n\n", soup.get_text().strip())


def scrape_markdown(relevant_results):
    markdowns = []
    for result in relevant_results:
        url = result["link"]
        response = requests.get(
            "https://scraping.narf.ai/api/v1/",
            params={
                "api_key": os.environ["SCRAPING_API_KEY"],
                "url": url,
                "render_js": "true",
            },
        )
        if response.ok:
            md = convert_html_to_markdown(response.text)
            markdowns.append(
                {
                    "url": url,
                    "title": result["title"],
                    "id": result["id"],
                    "markdown": md,
                }
            )
    return markdowns


def summarize_pages(markdowns):
    llm = ChatOpenAI(model="gpt-4o")
    prompt = load_prompt("summarise_markdown_page")
    chain = ChatPromptTemplate.from_messages([("system", prompt)]) | llm
    summaries = []
    for m in markdowns:
        try:
            summary = chain.invoke(
                {"markdown_input": " ".join(m["markdown"].split()[:2000])}
            )
            summaries.append({"markdown_summary": summary.content, "url": m["url"]})
        except:
            continue
    return summaries


class State(TypedDict):
    messages: Annotated[list, add_messages]
    summaries: List[dict]
    approved: bool
    created_summaries: Annotated[
        List[dict], Field(description="The summaries that have been created")
    ]


def run_review_graph(summaries):
    email_template = load_prompt("email_template")
    llm = ChatOpenAI(model="gpt-4o")

    class SummariserOutput(BaseModel):
        email_summary: str
        message: str

    summariser = ChatPromptTemplate.from_messages(
        [("system", load_prompt("summariser")), ("placeholder", "{messages}")]
    )
    summariser_chain = summariser | llm.with_structured_output(SummariserOutput)

    def summariser_fn(state: State):
        out = summariser_chain.invoke(
            {
                "messages": state["messages"],
                "list_of_summaries": state["summaries"],
                "input_template": email_template,
            }
        )
        return {
            "messages": [
                AIMessage(content=out.email_summary),
                AIMessage(content=out.message),
            ],
            "created_summaries": [out.email_summary],
        }

    class ReviewerOutput(BaseModel):
        approved: bool
        message: str

    reviewer = ChatPromptTemplate.from_messages(
        [("system", load_prompt("reviewer")), ("placeholder", "{messages}")]
    )
    reviewer_chain = reviewer | llm.with_structured_output(ReviewerOutput)

    def reviewer_fn(state: State):
        msgs = [
            (
                HumanMessage(content=m.content)
                if isinstance(m, AIMessage)
                else AIMessage(content=m.content)
            )
            for m in state["messages"]
        ]
        state["messages"] = msgs
        out = reviewer_chain.invoke({"messages": state["messages"]})
        return {
            "messages": [HumanMessage(content=out.message)],
            "approved": out.approved,
        }

    def decide(state: State):
        return END if state["approved"] else "summariser"

    builder = StateGraph(State)
    builder.add_node("summariser", summariser_fn)
    builder.add_node("reviewer", reviewer_fn)
    builder.add_edge(START, "summariser")
    builder.add_edge("summariser", "reviewer")
    builder.add_conditional_edges("reviewer", decide)

    graph = builder.compile()
    out = graph.invoke({"summaries": summaries})
    return out["created_summaries"][-1]


def send_email(to_email, to_name, email_content):
    config = sib_api_v3_sdk.Configuration()
    config.api_key["api-key"] = os.environ["SENDINGBLUE_API_KEY"]
    api_instance = sib_api_v3_sdk.TransactionalEmailsApi(
        sib_api_v3_sdk.ApiClient(config)
    )
    # Add greeting and thank you note
    personalized_content = f"""
        <html>
        <body>
            <p>Dear {to_name},</p>
            <p>We hope you're doing well!</p>
            {email_content}
            <p>Thank you for choosing Lunathink. We're honored to support your research journey.</p>
            <p>Warm regards,<br>Lunathink Team</p>
        </body>
        </html>
        """
    email_params = {
        "subject": "Your Personalized AI Research Summary",
        "sender": {"name": "Lunathink", "email": "apikey214@gmail.com"},
        "html_content": personalized_content,
        "to": [{"email": to_email, "name": to_name}],
    }
    try:
        api_instance.send_transac_email(sib_api_v3_sdk.SendSmtpEmail(**email_params))
    except ApiException as e:
        raise RuntimeError(f"Email sending failed: {e}")


def run_pipeline(search_terms: List[str], name: str, email: str):
    relevant_results = []
    for search_term in search_terms:
        python_results = search_serper(search_term)
        results = check_search_relevance(python_results)

        # Get the relevant result IDs from the LLM output
        relevant_ids = [r.id for r in results.relevant_results]

        # Filter original results to only include those with matching IDs
        filtered_results = [r for r in python_results if str(r["id"]) in relevant_ids]

        relevant_results.extend(filtered_results)

    markdowns = scrape_markdown(relevant_results)
    summaries = summarize_pages(markdowns)
    email_summary = run_review_graph(summaries)
    send_email(email, name, email_summary)
    return True
