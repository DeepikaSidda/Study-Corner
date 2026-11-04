"""Draw the Study Corner architecture with AWS icons (the `diagrams` library + Graphviz).

    pip install -r requirements-dev.txt     # includes diagrams
    python docs/architecture.py             # writes docs/architecture.png

Read it left to right: the student adds a lecture, the words are found
(Amazon Transcribe and Translate, or YouTube captions), the Strands agents
work on Amazon Bedrock, and everything is stored in Amazon DynamoDB.
"""

from pathlib import Path

from diagrams import Cluster, Diagram, Edge
from diagrams.aws.database import Dynamodb
from diagrams.aws.general import Disk, InternetAlt1, Users
from diagrams.aws.ml import Bedrock, Transcribe, Translate
from diagrams.aws.robotics import Robotics
from diagrams.aws.security import IAMAWSSts
from diagrams.aws.storage import S3
from diagrams.programming.framework import Starlette

OUT = Path(__file__).with_name("architecture")

graph = {"fontsize": "28", "fontname": "Helvetica-Bold", "pad": "0.5", "nodesep": "0.5", "ranksep": "1.2",
         "dpi": "130", "labelloc": "t", "splines": "spline", "newrank": "true"}
node = {"fontsize": "13", "fontname": "Helvetica"}
edge = {"fontsize": "12", "fontname": "Helvetica", "color": "#64748b", "penwidth": "1.4"}


def box(fill: str, line: str) -> dict:
    # Each Cluster needs its own label: Graphviz merges clusters with the same name.
    return {"bgcolor": fill, "pencolor": line, "fontsize": "16", "fontname": "Helvetica-Bold",
            "margin": "18", "style": "rounded"}


BLUE, ORANGE, GREEN = "#2563eb", "#f59e0b", "#16a34a"

with Diagram("Study Corner: architecture", filename=str(OUT), outformat="png", show=False,
             direction="LR", graph_attr=graph, node_attr=node, edge_attr=edge):

    student = Users("Student")

    with Cluster("1  Add a lecture", graph_attr=box("#f1f5f9", "#94a3b8")):
        web = Starlette("Study Corner web app\n(Python · Starlette)")
        sts = IAMAWSSts("AWS STS\nchecks the account")

    with Cluster("2  Get the words, with times", graph_attr=box("#fff7ed", ORANGE)):
        with Cluster("Upload or Creative Commons video", graph_attr=box("#ffedd5", "#fdba74")):
            s3 = S3("Amazon S3\naudio, deleted after")
            transcribe = Transcribe("Amazon Transcribe\nspeech to text\n+ language detection")
        youtube = InternetAlt1("YouTube Data API\nlicence check · captions")
        translate = Translate("Amazon Translate\nHindi · Tamil · Telugu\nto English")

    with Cluster("3  Strands Agents SDK", graph_attr=box("#eff6ff", BLUE)):
        board = Robotics("Board reader\nreads slides (vision)")
        notes = Robotics("Note writer\nsummary · notes · terms")
        quiz = Robotics("Quiz agent\nwrites and fixes questions")
        judge = Robotics("Judge agent\napproves each answer")
        chat = Robotics("Ask the lecture\ncited chat")
        bedrock = Bedrock("Amazon Bedrock · Nova Pro\nruns every agent")

    with Cluster("4  Stored and shown", graph_attr=box("#f0fdf4", GREEN)):
        dynamo = Dynamodb("Amazon DynamoDB\nnotes · quiz · transcript\nboard · search")
        files = Disk("Lecture folder\nvideo · frames · PDF")

    # 1 -> 2: the words
    student >> Edge(label="video file or\nYouTube link", color="#334155", penwidth="2") >> web
    web - Edge(style="dashed", color="#94a3b8") - sts
    web >> Edge(label="audio") >> s3 >> transcribe
    web >> youtube
    transcribe >> Edge(label="if not English", style="dashed") >> translate
    youtube >> Edge(style="dashed") >> translate

    # 2 -> 3: timed transcript to the agents
    for src in (transcribe, youtube, translate):
        src >> Edge(color=BLUE) >> notes
    # minlen pushes the board reader right, next to the other agents
    s3 >> Edge(label="video frames", color=BLUE, style="dashed", minlen="3") >> board
    notes >> Edge(color=BLUE) >> quiz
    quiz >> Edge(label="check_question", color=BLUE, penwidth="2.2") >> judge
    notes >> Edge(color=BLUE, style="dotted") >> chat

    # One line places Bedrock beside the agents without crossing the picture.
    judge - Edge(color="#0d9488", style="dotted") - bedrock

    # 3 -> 4: stored and searched
    judge >> Edge(label="notes · quiz\ntranscript · board", color=GREEN, penwidth="2") >> dynamo
    chat >> Edge(label="search · read", color=GREEN, dir="both") >> dynamo
    board >> Edge(color="#94a3b8", style="dotted", minlen="2") >> files

print(f"wrote {OUT.with_suffix('.png')}")
