package example;

/* metamodel_version: 1.11.0 */
/* version: 0.1.0 */
import java.net.URI;
import java.time.LocalDate;
import java.time.LocalTime;
import java.time.ZonedDateTime;
import java.util.List;
import lombok.*;

@Data
@EqualsAndHashCode(callSuper=false)
public class ToolCallPayload extends Payload {

  private ToolRef tool;
  private String argsRef;
  private String resultRef;
  private String sideEffect;
  private String effectClass;
  private String error;
  private List<String> used;


}