MINDMAPS := $(wildcard *.mindmap.yml)
INPUTS := $(wildcard *.plantuml.txt)
OUTPUTS := $(INPUTS:.txt=.png)

all: plantuml.jar $(MINDMAPS) $(OUTPUTS)

$(OUTPUTS): $(INPUTS) $(MINDMAPS)
	java -jar plantuml.jar -Iplantuml_options.txt -tpng $(INPUTS)

plantuml.jar:
	wget http://jaist.dl.sourceforge.net/project/plantuml/plantuml.jar || curl --output plantuml.jar http://jaist.dl.sourceforge.net/project/plantuml/plantuml.jar
